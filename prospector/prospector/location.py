"""Work out where a showroom really is from its postcode.

A Maps search for "kitchen showroom Birmingham" also returns places in
Nottingham or Stoke, so the searched town can't be trusted. Region comes from
postcodes.io (free, no key), with a postcode-area table as a fallback.
"""

import re

import requests

API = "https://api.postcodes.io/postcodes"

_REGION_NAMES = {
    "Yorkshire and The Humber": "Yorkshire & Humber",
    "East of England": "East of England",
}

_POSTCODE = re.compile(r"^([A-Z]{1,2})(\d[A-Z\d]?)\s*(\d[A-Z]{2})$")

# Fallback when postcodes.io has no answer. Areas that straddle a region
# border are given their main region.
AREA_REGION = {
    "E": "London", "EC": "London", "N": "London", "NW": "London", "SE": "London", "SW": "London",
    "W": "London", "WC": "London", "BR": "London", "CR": "London", "DA": "South East", "EN": "London",
    "HA": "London", "IG": "London", "KT": "London", "RM": "London", "SM": "London", "TW": "London",
    "UB": "London",
    "BN": "South East", "CT": "South East", "GU": "South East", "HP": "South East", "ME": "South East",
    "MK": "South East", "OX": "South East", "PO": "South East", "RG": "South East", "RH": "South East",
    "SL": "South East", "SO": "South East", "TN": "South East",
    "BA": "South West", "BH": "South West", "BS": "South West", "DT": "South West", "EX": "South West",
    "GL": "South West", "PL": "South West", "SN": "South West", "SP": "South West", "TA": "South West",
    "TQ": "South West", "TR": "South West",
    "AL": "East of England", "CB": "East of England", "CM": "East of England", "CO": "East of England",
    "IP": "East of England", "LU": "East of England", "NR": "East of England", "PE": "East of England",
    "SG": "East of England", "SS": "East of England", "WD": "East of England",
    "DE": "East Midlands", "LE": "East Midlands", "LN": "East Midlands", "NG": "East Midlands",
    "NN": "East Midlands",
    "B": "West Midlands", "CV": "West Midlands", "DY": "West Midlands", "HR": "West Midlands",
    "ST": "West Midlands", "SY": "West Midlands", "TF": "West Midlands", "WR": "West Midlands",
    "WS": "West Midlands", "WV": "West Midlands",
    "BD": "Yorkshire & Humber", "DN": "Yorkshire & Humber", "HD": "Yorkshire & Humber",
    "HG": "Yorkshire & Humber", "HU": "Yorkshire & Humber", "HX": "Yorkshire & Humber",
    "LS": "Yorkshire & Humber", "S": "Yorkshire & Humber", "WF": "Yorkshire & Humber",
    "YO": "Yorkshire & Humber",
    "BB": "North West", "BL": "North West", "CA": "North West", "CH": "North West", "CW": "North West",
    "FY": "North West", "L": "North West", "LA": "North West", "M": "North West", "OL": "North West",
    "PR": "North West", "SK": "North West", "WA": "North West", "WN": "North West",
    "DH": "North East", "DL": "North East", "NE": "North East", "SR": "North East", "TS": "North East",
    "CF": "Wales", "LD": "Wales", "LL": "Wales", "NP": "Wales", "SA": "Wales",
    "BT": "Northern Ireland",
    "AB": "Scotland", "DD": "Scotland", "DG": "Scotland", "EH": "Scotland", "FK": "Scotland",
    "G": "Scotland", "HS": "Scotland", "IV": "Scotland", "KA": "Scotland", "KW": "Scotland",
    "KY": "Scotland", "ML": "Scotland", "PA": "Scotland", "PH": "Scotland", "TD": "Scotland",
    "ZE": "Scotland",
}

_NOT_ENGLAND = {"Wales", "Scotland", "Northern Ireland"}


def normalise(postcode: str) -> str:
    """' cv344ab ' -> 'CV34 4AB'. Returns '' if it isn't a full UK postcode."""
    pc = re.sub(r"\s+", "", (postcode or "").upper())
    m = _POSTCODE.match(pc)
    return f"{m.group(1)}{m.group(2)} {m.group(3)}" if m else ""


def area(postcode: str) -> str:
    """'CV34 4AB' -> 'CV'."""
    m = re.match(r"^([A-Z]{1,2})\d", (postcode or "").upper().strip())
    return m.group(1) if m else ""


def sort_key(postcode: str):
    """Orders postcodes the way you'd read them: B1, B2 ... B10, then CV1 ..."""
    pc = normalise(postcode)
    if not pc:
        return ("~",)  # no postcode: last
    out, inward = pc.split()
    m = re.match(r"([A-Z]+)(\d+)([A-Z]?)", out)
    return (m.group(1), int(m.group(2)), m.group(3), inward)


def lookup(postcodes, session=None):
    """{postcode: {'region', 'country'}} for each postcode postcodes.io knows.

    Postcodes it doesn't know (or all of them, if it can't be reached) are
    left out; callers fall back to region_from_area().
    """
    session = session or requests
    wanted = sorted({p for p in (normalise(x) for x in postcodes) if p})
    found = {}
    for i in range(0, len(wanted), 100):
        batch = wanted[i:i + 100]
        try:
            r = session.post(API, json={"postcodes": batch}, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  postcodes.io lookup failed ({e}); using postcode areas instead")
            return found
        for row in r.json().get("result") or []:
            res = row.get("result")
            if not res:
                continue
            country = res.get("country") or ""
            region = country if country in _NOT_ENGLAND else _REGION_NAMES.get(res.get("region"), res.get("region") or "")
            found[normalise(row["query"])] = {"region": region, "country": country}
    return found


def region_from_area(postcode: str) -> str:
    return AREA_REGION.get(area(postcode), "")


def country_of_region(region: str) -> str:
    if not region:
        return ""
    return region if region in _NOT_ENGLAND else "England"
