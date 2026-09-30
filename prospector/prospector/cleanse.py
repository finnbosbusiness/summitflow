"""Showroom-only cleanse: decide whether a prospect is an independent kitchen
showroom worth calling, and spot duplicates of the same business.

Used by the prospect run on every new find, and to cleanse the rows already
on the Prospects tab (rows it removes are kept on the All Prospects tab with
the reason, so nothing is lost)."""

import re

from .matching import clean_name, distinctive_words, domain, outward_postcode

ALL_TAB = "All Prospects"
KEPT = "Kept"

# A kitchen business says so in its name or its web address.
_KITCHEN = re.compile(r"kitchen|k[uü]chen|kbb|cabinet|cucina|keuken", re.I)
# Words that mean the business has somewhere to visit.
_SHOWROOM = re.compile(r"showroom|studio|design centre|kitchen centre|\bcentre\b", re.I)


def _words(*words):
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I)


# Food, drink and other businesses that only share the word "kitchen".
_NOT_KITCHEN_TRADE = _words(
    "cafe", "café", "bistro", "deli", "restaurant", "takeaway", "bar", "pub", "catering", "caterers?",
    "bakery", "cake", "fudge", "chocolate", "sweets?", "salad", "sandwich(es)?", "food", "foods",
    "nails?", "salon", "beauty", "wellness", "yoga", "pilates", "charity", "foodbank", "community",
    "commercial kitchens?", "outdoor kitchens?", "kitchenware", "cookware", "cookshop", "kitchen shop",
    "kitchen table", "soup", "grill", "diner", "eatery", "canteen", "kitchen garden", "gifts?",
    "essentials", "accessories", "kitchen collection", "splashbacks?",
)
_NOT_KITCHEN_CATEGORY = re.compile(
    r"restaurant|cafe|bistro|bar\b|deli|bakery|cake|confection|chocolate|caterer|food|grocery|market|salon|"
    r"beauty|health|sandwich|salad|steak|non-profit|garden cent|painter",
    re.I,
)
_WORKTOPS = _words("worktops?", "quartz", "granite", "marble", "stone ?works", "surfaces?", "countertops?")
_WHOLESALE = _words("wholesale(rs)?", "distribution", "components", "merchants?", "mouldings", "doors? supplies")
# "Trade Kitchens" showrooms sell to the public, so these only count
# without a kitchen in the name.
_TRADE = _words("trade", "supplies", "suppliers?")
_TRADE_CATEGORY = re.compile(r"wholesaler|suppliers", re.I)
# Fitters and builders install kitchens but have no showroom to sell from.
_FITTER = _words(
    "fitters?", "fitting", "installations?", "installers?", "refurb(ishments?)?", "resprays?", "spray(ing)?",
    "revamps?", "makeovers?", "renovations?", "remodel(l?ing|ers?)?", "builders?", "building", "construction",
    "contractors?", "contracts", "developments?", "property", "extensions?", "conversions?", "handyman",
    "maintenance", "fit ?out", "painters?", "hand ?painted", "painting", "plumbing", "multitrade", "build",
    "builds", "freelance", "mobile",
)
# Shops that sell appliances rather than kitchens.
_APPLIANCES = _words("appliances?", "range cookers?", "rangecookers", "electricals?")
# Shops that sell furniture or homeware, when the name doesn't say kitchen.
_OTHER_RETAIL = _words(
    "antiques?", "clearance", "discount",
    "emporium", "beds?", "bed centre", "sofas?", "lighting", "taps", "garden", "flooring", "floors?",
    "carpets?", "curtains?", "blinds", "home ?store", "homeware", "warehouse", "outlet store",
)
# Makers without a kitchen focus: joinery and furniture workshops.
_WORKSHOP = _words("joinery", "joiners?", "carpentry", "carpenters?", "woodwork(s|ing)?", "furniture",
                   "furnishings", "furniture makers?")

# A UK postcode starts with letters; "01453" is a US zip code.
_UK_POSTCODE = re.compile(r"^[A-Z]{1,2}\d", re.I)
_UK_PHONE = re.compile(r"^\s*(0|\+?44)")

def reason(p):
    """'' if this looks like an independent kitchen showroom, else why not.

    `p` is a Prospects row dict (field names as in columns.INPUT_COLUMNS).
    Chains are handled separately (they need the Chain Exclusions list).
    """
    name = str(p.get("company", "")).strip()
    site = str(p.get("website", ""))
    category = str(p.get("google_category", ""))
    kitchen = bool(_KITCHEN.search(name) or _KITCHEN.search(domain(site)))
    showroom_word = bool(_SHOWROOM.search(name))

    postcode = str(p.get("postcode", "")).strip()
    phone = str(p.get("phone", "")).strip()
    if (postcode and not _UK_POSTCODE.match(postcode)) or (phone and not _UK_PHONE.match(phone)):
        return "Not in the UK"
    if str(p.get("company_status", "")).strip() == "Liquidation":
        return "In liquidation"
    if _NOT_KITCHEN_TRADE.search(name) or _NOT_KITCHEN_CATEGORY.search(category):
        return "Not a kitchen business"
    # "Kitchens & Worktops" sells kitchens; "Kitchen Worktops Sussex" doesn't.
    if _WORKTOPS.search(name) and not re.search(r"kitchens\b|kbb", name, re.I):
        return "Worktop or stone supplier"
    if _WHOLESALE.search(name) or (not kitchen and (_TRADE.search(name) or _TRADE_CATEGORY.search(category))):
        return "Trade supplier"
    if _FITTER.search(name) and not showroom_word:
        return "Fitter or builder, no showroom"
    if _APPLIANCES.search(name) and not re.search(r"kitchens|showroom|kbb", name, re.I):
        return "Appliance shop"
    if _OTHER_RETAIL.search(name) and not kitchen:
        return "Furniture or homeware shop"
    if not kitchen and _WORKSHOP.search(name):
        return "Joinery or furniture maker, not kitchens"
    if not str(p.get("physical_showroom", "")).strip() and not showroom_word:
        return "No showroom on Google Maps"
    if not kitchen:
        return "Kitchens not in name or website"
    if not re.sub(r"\D", "", str(p.get("phone", ""))):
        return "No phone number"
    return ""


def phone_key(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits.startswith("44"):
        digits = "0" + digits[2:]
    return digits if len(digits) >= 10 else ""


def duplicate_keys(p):
    """Identities that mean two rows are the same business: the same phone
    number, or the same name in the same postcode area."""
    out = []
    ph = phone_key(p.get("phone"))
    if ph:
        out.append("t:" + ph)
    n = clean_name(p.get("company", ""))
    area = outward_postcode(str(p.get("postcode", "")))
    if n and area:
        out.append("n:" + n + "|" + area)
    return out


def _completeness(p):
    """Which of two duplicates to keep: the one with more to go on."""
    reviews = str(p.get("google_reviews", "")).replace(",", "")
    return (
        bool(domain(str(p.get("website", "")))),
        bool(str(p.get("company_number", "")).strip()),
        int(reviews) if reviews.isdigit() else 0,
    )


def find_duplicates(rows):
    """rows: list of row dicts in sheet order. Returns {index: index_kept}
    for every row that duplicates a better row.

    Company numbers aren't used: a generic name like "Victoria Kitchens" is
    matched to the same company wherever the showroom is."""
    groups = {}
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, p in enumerate(rows):
        for k in duplicate_keys(p):
            if k in groups:
                parent[find(i)] = find(groups[k])
            else:
                groups[k] = i

    members = {}
    for i in range(len(rows)):
        members.setdefault(find(i), []).append(i)
    dupes = {}
    for group in members.values():
        if len(group) < 2:
            continue
        best = max(group, key=lambda i: (_completeness(rows[i]), -i))
        for i in group:
            if i != best:
                dupes[i] = best
    return dupes


def unreliable_company_numbers(rows):
    """Companies House numbers that can't be trusted: matched to a name with
    nothing distinctive in it, or shared by showrooms with different names
    (one 'Kitchens & Bathrooms Ltd' matched to eight differently named
    showrooms) or in different postcode areas. The same rows would get no
    match from companies_house.best_match now."""
    names, areas = {}, {}
    for p in rows:
        number = str(p.get("company_number", "")).strip()
        if number:
            names.setdefault(number, set()).add(frozenset(distinctive_words(p.get("company", ""))))
            areas.setdefault(number, set()).add(outward_postcode(str(p.get("postcode", ""))))
    return {n for n in names if len(names[n]) > 1 or len(areas[n]) > 1 or names[n] == {frozenset()}}
