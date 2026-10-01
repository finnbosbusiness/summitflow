"""Turn a Google Maps / Places result into a Prospects row, and decide
whether it is an independent kitchen showroom at all."""

import re
from urllib.parse import urlparse

from .location import clean_town, normalise
from .matching import domain
from .niches import bathroom_reason, roofing_reason

QUERY = "kitchen showroom {town}"

_RELEVANT = ("kitchen", "cabinet")
_PHYSICAL = ("store", "showroom", "shop")
# Businesses that mention kitchens but aren't kitchen showrooms.
_NOT_SHOWROOM = (
    "coffee", "cafe", "restaurant", "takeaway", "heating", "plumb", "boiler",
    "builders merchant", "building materials", "building supplies", "timber",
    "appliance", "electrical", "countertop", "worktop", "granite", "quartz",
    "hardware", "diy", "cleaning", "catering", "food", "bakery", "meal", "plumber", "gift",
)
# Google's main category for businesses that can be kitchen showrooms.
_SHOWROOM_TYPES = (
    "furniture store", "general contractor", "home improvement", "home goods", "manufacturer",
    "interior designer", "kitchen", "cabinet", "carpenter", "joiner", "designer",
)
# Name words that mean some other trade, when the name doesn't say kitchen.
_OTHER_TRADES = (
    "bathroom", "bedroom", "tile", "flooring", "door", "window", "taps", "bed centre", "beds",
    "garden", "fire", "stove", "marble", "stone", "spray", "wardrobe", "sofa", "blind", "curtain",
    "carpet", "furnishers", "gift", "lighting",
)
# A website path like /showrooms/solihull means one branch of a bigger business.
_BRANCH_PATH = re.compile(r"/(showrooms?|branch(es)?|stores?|locations?)/[^/]+", re.I)


def uk_phone(phone: str) -> str:
    """'+44 1926 000000' -> '01926 000000', so it reads the way you'd dial it."""
    phone = (phone or "").strip()
    if phone.startswith("+44"):
        phone = "0" + phone[3:].lstrip()
    return phone


def clean_url(url: str) -> str:
    """Drop tracking parameters: '...co.uk/?utm_source=gmb' -> '...co.uk/'."""
    url = (url or "").strip()
    if not url:
        return ""
    u = urlparse(url if "://" in url else "http://" + url)
    return f"{u.scheme}://{u.netloc}{u.path or '/'}"


def is_branch_page(url: str) -> bool:
    return bool(_BRANCH_PATH.search(urlparse(url or "").path or ""))


def _categories(item):
    cats = list(item.get("categories") or [])
    if item.get("categoryName"):
        cats.append(item["categoryName"])
    return [c.lower() for c in cats]


def to_prospect(item, town_regions, niche="kitchen", query=QUERY):
    """Convert one Apify place into a Prospects row dict.

    Returns (row, None) or (None, reason) where reason is 'closed',
    'not kitchen' or 'branch'.

    Region is filled in later from the postcode. Town is the showroom's own
    town from its Maps address, falling back to the town that was searched.
    """
    if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
        return None, "closed"
    name = (item.get("title") or "").strip()
    cats = _categories(item)
    lname = name.lower()
    if item.get("countryCode") and item["countryCode"] != "GB":
        return None, "outside UK"
    if niche == "roofing":
        why = roofing_reason(name, cats, item.get("categoryName") or "")
        if not name or why:
            return None, why or "not roofing"
        return _row(item, name, town_regions, query, showroom=False), None
    if niche == "bathroom":
        why = bathroom_reason(name, cats, item.get("categoryName") or "")
        if not name or why:
            return None, why or "not bathroom"
        if is_branch_page(item.get("website") or ""):
            return None, "branch"
        return _row(item, name, town_regions, query, showroom=True), None
    kitchen = "kitchen" in lname or any(k in c for c in cats for k in _RELEVANT)
    if item.get("_trusted") and not kitchen:
        # Google matched it to "kitchen showroom", but without "kitchen" in
        # the name it must also be the kind of business a showroom is listed
        # as, and not say it's something else (bathroom-only, taps, beds).
        kitchen = "kbb" in lname or (
            any(t in (item.get("categoryName") or "").lower() for t in _SHOWROOM_TYPES)
            and not any(w in lname for w in _OTHER_TRADES)
        )
    # The main Maps category decides it ("Appliance store", "Coffee machine
    # supplier"). The name only counts when it doesn't say kitchen, so
    # "Connelly's Kitchens & Appliances" stays in.
    main_cat = (item.get("categoryName") or "").lower()
    off_topic = any(w in main_cat for w in _NOT_SHOWROOM) or (
        "kitchen" not in lname and any(w in lname for w in _NOT_SHOWROOM)
    )
    if not name or not kitchen or off_topic:
        return None, "not kitchen"

    if is_branch_page(item.get("website") or ""):
        return None, "branch"
    return _row(item, name, town_regions, query, showroom=True), None


def _row(item, name, town_regions, query, showroom):
    website = item.get("website") or ""
    cats = _categories(item)
    search = (item.get("searchString") or "").strip()
    prefix = query.format(town="")
    searched = search[len(prefix):].strip() if search.lower().startswith(prefix.lower()) else ""
    town = clean_town((item.get("city") or "").strip() or searched, item.get("address") or "",
                      item.get("postalCode") or "")

    return {
        "company": name,
        "website": clean_url(website) if domain(website) else "",
        "phone": uk_phone(item.get("phone") or item.get("phoneUnformatted") or ""),
        "town": town,
        "postcode": normalise(item.get("postalCode") or "") or (item.get("postalCode") or ""),
        "region": "",
        "source": "Google Maps",
        # Y when Maps lists it as a store/showroom. Blank (not N) otherwise:
        # a "Kitchen remodeler" listing may still have a showroom.
        "physical_showroom": "Y" if showroom and any(k in c for c in cats for k in _PHYSICAL) else "",
        "google_rating": item.get("totalScore") or "",
        "google_reviews": item.get("reviewsCount") or "",
        "address": item.get("address") or "",
        "maps_url": item.get("mapsUrl") or "",
        "google_category": item.get("categoryName") or "",
        "place_id": item.get("placeId") or "",
        "_searched_region": town_regions.get(searched, ""),
    }


MULTI_SITE = 3


def multi_site_domains(items, threshold: int = MULTI_SITE):
    """Website domains listed at `threshold` or more different postcodes in
    one run: branches of a multi-site business, even if it isn't on the
    Chain Exclusions tab yet."""
    seen = {}
    for it in items:
        d = domain(it.get("website") or "")
        pc = (it.get("postalCode") or "").replace(" ", "").upper()
        if d and pc:
            seen.setdefault(d, set()).add(pc)
    return {d for d, pcs in seen.items() if len(pcs) >= threshold}
