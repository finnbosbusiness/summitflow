"""Column layout of the Prospects tab in the Cold Lead Template.

Only the input (light-header) columns are ever written. The automatic
(dark-header) columns hold formulas pre-filled down to LAST_ROW and must
never be touched.
"""

FIRST_ROW = 4
LAST_ROW = 10003

# field name -> column letter, input columns only
INPUT_COLUMNS = {
    "company": "B",
    "website": "C",
    "phone": "D",
    "town": "E",
    "postcode": "F",
    "region": "G",
    "source": "H",
    "physical_showroom": "J",
    "google_rating": "K",
    "google_reviews": "L",
    "running_meta_ads": "M",
    "oldest_ad_start": "N",
    "meta_ad_hook": "P",
    "running_google_ads": "Q",
    "search_result": "R",
    "competitor_ranking": "T",
    "premium_brands": "U",
    "positioning": "V",
    "budget_signals": "W",
    "company_number": "X",
    "company_status": "Y",
    "incorporated": "Z",
    "decision_maker_name": "AB",
    "decision_maker_role": "AC",
    "email": "AD",
    "tps_ctps": "AE",
    "address": "AO",
    "maps_url": "AP",
    "google_category": "AQ",
    "place_id": "AR",
}

FORMULA_COLUMNS = {"I", "O", "S", "AA", "AF", "AG", "AH", "AI", "AJ", "AK", "AL", "AM", "AN"}

REGIONS = [
    "London", "South East", "South West", "East of England", "East Midlands",
    "West Midlands", "Yorkshire & Humber", "North West", "North East",
    "Wales", "Scotland", "Northern Ireland",
]


def col_index(letter: str) -> int:
    """'A' -> 0, 'AB' -> 27."""
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def col_letter(index: int) -> str:
    index += 1
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def contiguous_blocks():
    """Group input columns into contiguous runs so each run is one write range.

    Returns a list of (first_letter, last_letter, [field, ...]).
    """
    by_index = sorted((col_index(c), f) for f, c in INPUT_COLUMNS.items())
    blocks, current = [], []
    for idx, field in by_index:
        if current and idx != current[-1][0] + 1:
            blocks.append(current)
            current = []
        current.append((idx, field))
    blocks.append(current)
    return [(col_letter(b[0][0]), col_letter(b[-1][0]), [f for _, f in b]) for b in blocks]


assert not FORMULA_COLUMNS & set(INPUT_COLUMNS.values())
