"""The trades the prospector searches for, and where each one's list lives."""

import re

NICHES = {
    "kitchen": {
        "query": "kitchen showroom {town}",
        "tab": "All Prospects",
        "skipped_tab": "Skipped",
        "last_row": 10003,
        "chains": True,  # skip the national chains on the Chain Exclusions tab
    },
    "roofing": {
        "query": "roofing company {town}",
        "tab": "Roofing Prospects",
        "skipped_tab": "Roofing Skipped",
        "last_row": 20003,
        # The Chain Exclusions tab lists kitchen chains ("Wren" would match
        # "Wrenn Roofing"); roofing merchants are filtered by name instead.
        "chains": False,
    },
}

# Businesses that turn up for "roofing company" but aren't roofers. Decided by
# name: Google lists plenty of real roofers as "Building materials store".
_NOT_ROOFER = re.compile(
    r"\b(roof ?top (bar|restaurant|terrace|garden)|roof ?garden|roof ?box(es)?|roof ?racks?|roof ?tents?|"
    r"roof ?bars?|supplies|supplier|superstore|merchants?|wholesale|warehouse|timber|building materials|"
    r"roofing (centre|center|sheets?|outlet)|truss(es)?|distribution|plant hire|skip hire|scaffold(ing)? hire|"
    r"restaurant|cafe|café|pub|hotel|kitchen|bistro|grill|"
    # national roofing merchants
    r"sig roofing|burton roofing|chandlers roofing|roofing outlaw|jewson|travis perkins|sydenhams|"
    r"roofing superstore|keyline|buildbase)\b",
    re.I,
)
_NOT_ROOFER_CATEGORY = re.compile(r"restaurant|\bbar\b|cafe|hotel|\bpub\b|car |auto", re.I)


def roofing_reason(name, categories, main_category):
    """'' if a Maps listing is a roofer, else why not."""
    lname = (name or "").lower()
    roofer = "roof" in lname or any("roof" in c for c in categories)
    if not roofer:
        return "not roofing"
    if _NOT_ROOFER.search(name or "") or _NOT_ROOFER_CATEGORY.search(main_category or ""):
        return "not a roofer"
    return ""
