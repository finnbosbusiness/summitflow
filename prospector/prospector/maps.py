"""Stage 1: find kitchen showrooms on Google Maps via the Apify
Google Maps scraper (compass/crawler-google-places)."""

import time

import requests

from .matching import domain

ACTOR = "compass~crawler-google-places"
API = "https://api.apify.com/v2"
QUERY = "kitchen showroom {town}"

_RELEVANT = ("kitchen", "cabinet")
_PHYSICAL = ("store", "showroom", "shop")


def run_search(token: str, towns, max_per_town: int = 40, timeout_s: int = 1800):
    """Run one Apify job covering every town and return the raw place items."""
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "searchStringsArray": [QUERY.format(town=t) for t in towns],
        "countryCode": "gb",
        "language": "en",
        "maxCrawledPlacesPerSearch": max_per_town,
        "skipClosedPlaces": True,
    }
    r = requests.post(f"{API}/acts/{ACTOR}/runs", json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    run = r.json()["data"]

    deadline = time.time() + timeout_s
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise TimeoutError(f"Apify run {run['id']} still running after {timeout_s}s")
        time.sleep(15)
        r = requests.get(f"{API}/actor-runs/{run['id']}", headers=headers, timeout=60)
        r.raise_for_status()
        run = r.json()["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run['id']} ended with status {run['status']}")

    items, offset = [], 0
    while True:
        r = requests.get(
            f"{API}/datasets/{run['defaultDatasetId']}/items",
            params={"clean": "true", "offset": offset, "limit": 1000},
            headers=headers,
            timeout=120,
        )
        r.raise_for_status()
        page = r.json()
        items.extend(page)
        if len(page) < 1000:
            return items
        offset += len(page)


def uk_phone(phone: str) -> str:
    """'+44 1926 000000' -> '01926 000000', so it reads the way you'd dial it."""
    phone = (phone or "").strip()
    if phone.startswith("+44"):
        phone = "0" + phone[3:].lstrip()
    return phone


def _categories(item):
    cats = list(item.get("categories") or [])
    if item.get("categoryName"):
        cats.append(item["categoryName"])
    return [c.lower() for c in cats]


def to_prospect(item, town_regions):
    """Convert one Apify place into a Prospects row dict, or None if it isn't
    a kitchen business or is closed.

    town_regions maps the searched town to its region. The searched town (not
    the Maps address town) goes in the Town column, so the call opener quotes
    the exact search that was run.
    """
    if item.get("permanentlyClosed") or item.get("temporarilyClosed"):
        return None
    name = (item.get("title") or "").strip()
    cats = _categories(item)
    if not name or not (
        "kitchen" in name.lower() or any(k in c for c in cats for k in _RELEVANT)
    ):
        return None

    search = (item.get("searchString") or "").strip()
    prefix = QUERY.format(town="")
    town = search[len(prefix):].strip() if search.lower().startswith(prefix.lower()) else (item.get("city") or "")
    town = next((t for t in town_regions if t.lower() == town.lower()), town)

    website = item.get("website") or ""
    return {
        "company": name,
        "website": website if domain(website) else "",
        "phone": uk_phone(item.get("phone") or item.get("phoneUnformatted") or ""),
        "town": town,
        "postcode": item.get("postalCode") or "",
        "region": town_regions.get(town, ""),
        "source": "Google Maps",
        # Y when Maps lists it as a store/showroom. Blank (not N) otherwise:
        # a "Kitchen remodeler" listing may still have a showroom.
        "physical_showroom": "Y" if any(k in c for c in cats for k in _PHYSICAL) else "",
        "google_rating": item.get("totalScore") or "",
        "google_reviews": item.get("reviewsCount") or "",
    }
