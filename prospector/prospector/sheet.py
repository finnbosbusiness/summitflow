"""Stage 7: read and write the Prospects tab without touching formula columns."""

import json
import os

from .columns import FIRST_ROW, INPUT_COLUMNS, LAST_ROW, col_index, contiguous_blocks
from .matching import phone_key, prospect_key


def keys_for(record):
    """Every identity a showroom can be matched on: Google's place ID, its
    website domain (or name plus postcode area), and its phone number."""
    out = []
    if str(record.get("place_id", "")).strip():
        out.append("p:" + str(record["place_id"]).strip())
    out.append(prospect_key(record.get("website", ""), record.get("company", ""), record.get("postcode", "")))
    phone = phone_key(record.get("phone"))
    if phone:
        out.append("t:" + phone)
    return out

TAB = "All Prospects"
_READ_RANGE = f"B{FIRST_ROW}:AR{LAST_ROW}"
SKIPPED_TAB = "Skipped"
_OFFSET = col_index("B")
# Written as text: Sheets would otherwise read "+44 ..." as a formula and
# drop the leading zero from phone and company numbers.
# Free text is protected too: an ad hook starting with "+" or "=" would
# otherwise be read as a formula.
_TEXT_FIELDS = {"phone", "company_number", "postcode", "meta_ad_hook", "competitor_ranking", "address"}


def _cell(field, value):
    if value is None:
        return None
    if field in _TEXT_FIELDS and value != "":
        return "'" + str(value)
    return value


def open_sheet(sheet_id: str):
    import gspread

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        gc = gspread.service_account_from_dict(json.loads(raw))
    else:
        gc = gspread.service_account(filename=os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    return gc.open_by_key(sheet_id)


def read_chains(spreadsheet):
    rows = spreadsheet.worksheet("Chain Exclusions").get("B4:B200")
    return [r[0].strip() for r in rows if r and r[0].strip()]


def read_prospects(spreadsheet):
    """Return {row_number: {field: value}} for every row on the Prospects tab,
    including blank ones (so we know where to add new rows)."""
    ws = spreadsheet.worksheet(TAB)
    grid = ws.get(_READ_RANGE)
    rows = {}
    for i in range(LAST_ROW - FIRST_ROW + 1):
        cells = grid[i] if i < len(grid) else []
        rows[FIRST_ROW + i] = {
            f: (cells[col_index(c) - _OFFSET] if col_index(c) - _OFFSET < len(cells) else "")
            for f, c in INPUT_COLUMNS.items()
        }
    return rows


class Plan:
    """Works out what to write where. Pure logic, no API calls, so it's testable.

    - A prospect already on the sheet (same website domain, or same name and
      postcode area) only has its blank input cells filled. Your edits are
      never overwritten.
    - A new prospect goes into the first row whose Company Name is blank.
      (Rows are pre-filled with formulas down to row 10003, so appending
      after the last row would land below the formulas.)
    """

    def __init__(self, existing_rows):
        self.rows = existing_rows
        self.index = {}
        self.free = []
        for n, r in existing_rows.items():
            if str(r.get("company", "")).strip():
                for k in keys_for(r):
                    self.index.setdefault(k, n)
            else:
                self.free.append(n)
        self.updates = {}  # row -> {field: value}, not yet written
        self.new_rows = set()
        self.updated_rows = set()
        self.added = 0
        self.out_of_room = 0

    def row_for(self, prospect):
        for k in keys_for(prospect):
            if k in self.index:
                return self.index[k]
        return None

    def upsert(self, prospect):
        """Returns the sheet row the prospect lives on, or None if the tab is full."""
        row = self.row_for(prospect)
        if row is None:
            if not self.free:
                self.out_of_room += 1
                return None
            row = self.free.pop(0)
            self.rows[row] = {f: "" for f in INPUT_COLUMNS}
            self.new_rows.add(row)
            self.added += 1
        for k in keys_for(prospect):
            self.index.setdefault(k, row)
        self.fill(row, prospect)
        return row

    def fill(self, row, values):
        current = self.rows[row]
        for field, value in values.items():
            if field not in INPUT_COLUMNS or value in ("", None):
                continue
            if str(current.get(field, "")).strip() == "":
                current[field] = value
                self.updates.setdefault(row, {})[field] = value
                self.updated_rows.add(row)

    def set(self, row, values):
        """Overwrite cells, including clearing them with "". Only used for
        the ad and search columns, which the script owns and refreshes."""
        current = self.rows[row]
        for field, value in values.items():
            if field not in INPUT_COLUMNS or value is None:
                continue
            if str(current.get(field, "")) != str(value):
                current[field] = value
                self.updates.setdefault(row, {})[field] = value
                self.updated_rows.add(row)

    @property
    def filled(self):
        """Existing rows that had blank cells filled in."""
        return len(self.updated_rows - self.new_rows)

    def value_ranges(self):
        """Sheets API batchUpdate payload for the pending updates. Runs of
        consecutive rows are written as one range per column block; None
        cells are skipped by the API, so untouched cells keep their value."""
        out = []
        rows = sorted(self.updates)
        runs, run = [], []
        for r in rows:
            if run and r != run[-1] + 1:
                runs.append(run)
                run = []
            run.append(r)
        if run:
            runs.append(run)
        for run in runs:
            for first, last, block in contiguous_blocks():
                if not any(f in self.updates[r] for r in run for f in block):
                    continue
                out.append({
                    "range": f"'{TAB}'!{first}{run[0]}:{last}{run[-1]}",
                    "values": [[_cell(f, self.updates[r].get(f)) for f in block] for r in run],
                })
        return out


def write(spreadsheet, plan, chunk=200):
    """Write pending updates and clear them, so a long run can save as it goes."""
    import time

    ranges = plan.value_ranges()
    for i in range(0, len(ranges), chunk):
        spreadsheet.values_batch_update({
            "valueInputOption": "USER_ENTERED",
            "data": ranges[i:i + chunk],
        })
        time.sleep(1.1)  # stay under the Sheets write quota
    plan.updates = {}
    return len(ranges)


SKIPPED_COLUMNS = ["reason", "company", "website", "phone", "postcode", "town", "google_category", "maps_url", "searched"]


def write_skipped(spreadsheet, skipped, last_row=20003):
    """Replace the Skipped tab's rows with this run's skipped businesses."""
    ws = spreadsheet.worksheet(SKIPPED_TAB)
    ws.batch_clear([f"B4:J{last_row}"])
    rows = [[("'" + str(s.get(c, "")) if c == "phone" and s.get(c) else s.get(c, "")) for c in SKIPPED_COLUMNS]
            for s in skipped[: last_row - 3]]
    if rows:
        ws.update(values=rows, range_name=f"B4:J{3 + len(rows)}", value_input_option="USER_ENTERED")
    return len(rows)
