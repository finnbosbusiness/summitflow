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
    "bathroom": {
        "query": "bathroom showroom {town}",
        "tab": "Bathroom Prospects",
        "skipped_tab": "Bathroom Skipped",
        "last_row": 20003,
        # National bathroom chains and plumbing merchants, matched like the
        # Chain Exclusions tab (case-insensitive, in the name or website).
        "chains": [
            "Bathstore", "Victoria Plumb", "Victorian Plumbing", "Easy Bathrooms", "Bathroom Takeaway",
            "Better Bathrooms", "BathEmpire", "Bathroom Discount Centre", "Mr Bathrooms", "Royal Bathrooms",
            "Ripples", "C.P. Hart", "CP Hart", "Porcelanosa", "Topps Tiles", "Tile Giant", "City Plumbing",
            "Plumb Center", "Plumbcenter", "Plumbase", "PTS Plumbing", "Graham Plumbers", "Screwfix",
            "Toolstation", "Wickes", "B&Q", "Homebase", "IKEA", "Howdens", "Magnet", "Wren", "Jewson",
            "Travis Perkins", "Selco", "Benchmarx", "BSS", "Bathroom Showroom Direct", "Tap Warehouse",
            "Bathroom Mountain", "Mypad", "Soak.com", "John Lewis", "Dunelm", "Harvey Norman",
        ],
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


_NOT_BATHROOM = re.compile(
    r"\b(plumbing (supplies|merchants?|centre|center)|plumbers? merchants?|builders? merchants?|merchants?|"
    r"supplies|wholesale|trade counter|heating supplies|boiler|radiators?|tiles? only|"
    r"restaurant|cafe|café|pub|hotel|salon|beauty|nails?|gym|care home|toilets? hire|portable toilets?|"
    r"loo hire|cleaning|cleaners?|resurfacing|re-?enamel)\b",
    re.I,
)
_NOT_BATHROOM_CATEGORY = re.compile(
    r"restaurant|\bbar\b|cafe|hotel|\bpub\b|day spa|salon|beauty|gym|hair|car |auto|"
    r"public bathroom|toilet|hospital|care home", re.I
)
# Plain "bath" isn't enough: Bath is a city ("Bath Kitchen Company").
_BATHROOM_WORDS = ("bathroom", "baths", "bath &", "bath and", "& bath", "and bath", "wet room", "wetroom",
                   "shower", "kbb", "sanitary", "bagno", "bathware")


def bathroom_reason(name, categories, main_category):
    """'' if a Maps listing is a bathroom showroom or bathroom business, else why not."""
    lname = (name or "").lower()
    bathroom = any(w in lname for w in _BATHROOM_WORDS) or any("bath" in c for c in categories)
    if not bathroom:
        return "not bathroom"
    if _NOT_BATHROOM.search(name or "") or _NOT_BATHROOM_CATEGORY.search(main_category or ""):
        return "not a bathroom showroom"
    return ""
