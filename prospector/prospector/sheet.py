"""Stage 7: read and write the Prospects tab without touching formula columns."""

import json
import os

from .columns import FIRST_ROW, INPUT_COLUMNS, LAST_ROW, col_index, contiguous_blocks
from .matching import prospect_key

TAB = "Prospects"
_READ_RANGE = f"B{FIRST_ROW}:AE{LAST_ROW}"
_OFFSET = col_index("B")
# Written as text: Sheets would otherwise read "+44 ..." as a formula and
# drop the leading zero from phone and company numbers.
# Free text is protected too: an ad hook starting with "+" or "=" would
# otherwise be read as a formula.
_TEXT_FIELDS = {"phone", "company_number", "postcode", "meta_ad_hook", "competitor_ranking"}


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
      (Rows are pre-filled with formulas down to row 3003, so appending
      after the last row would land below the formulas.)
    """

    def __init__(self, existing_rows):
        self.rows = existing_rows
        self.index = {}
        self.free = []
        for n, r in existing_rows.items():
            if str(r.get("company", "")).strip():
                self.index.setdefault(prospect_key(r.get("website", ""), r["company"], r.get("postcode", "")), n)
            else:
                self.free.append(n)
        self.updates = {}  # row -> {field: value}
        self.new_rows = set()
        self.added = 0
        self.out_of_room = 0

    def row_for(self, prospect):
        key = prospect_key(prospect.get("website", ""), prospect["company"], prospect.get("postcode", ""))
        return self.index.get(key)

    def upsert(self, prospect):
        """Returns the sheet row the prospect lives on, or None if the tab is full."""
        row = self.row_for(prospect)
        if row is None:
            if not self.free:
                self.out_of_room += 1
                return None
            row = self.free.pop(0)
            self.rows[row] = {f: "" for f in INPUT_COLUMNS}
            key = prospect_key(prospect.get("website", ""), prospect["company"], prospect.get("postcode", ""))
            self.index[key] = row
            self.new_rows.add(row)
            self.added += 1
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

    @property
    def filled(self):
        """Existing rows that had blank cells filled in."""
        return len(set(self.updates) - self.new_rows)

    def value_ranges(self):
        """Sheets API batchUpdate payload. None cells are skipped by the API,
        so untouched cells inside a range keep their current value."""
        out = []
        for row, fields in sorted(self.updates.items()):
            for first, last, block in contiguous_blocks():
                if any(f in fields for f in block):
                    out.append({
                        "range": f"{TAB}!{first}{row}:{last}{row}",
                        "values": [[_cell(f, fields.get(f)) for f in block]],
                    })
        return out


def write(spreadsheet, plan, chunk=400):
    ranges = plan.value_ranges()
    for i in range(0, len(ranges), chunk):
        spreadsheet.values_batch_update({
            "valueInputOption": "USER_ENTERED",
            "data": ranges[i:i + chunk],
        })
    return len(ranges)
