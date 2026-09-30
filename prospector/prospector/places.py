"""Stage 1 (and the map pack for stage 3): find kitchen showrooms with
Google's Places API (Text Search).

Google gives a monthly free allowance for Text Search, so a search per town
costs nothing at this volume. Each result is converted to the same shape the
rest of the pipeline already reads (the Apify Google Maps format).
"""

import time

import requests

API = "https://places.googleapis.com/v1/places:searchText"
PAGE_SIZE = 20  # Google's maximum per page
FIELDS = ",".join([
    "places.id", "places.displayName", "places.addressComponents", "places.websiteUri",
    "places.nationalPhoneNumber", "places.rating", "places.userRatingCount", "places.types",
    "places.primaryTypeDisplayName", "places.businessStatus", "nextPageToken",
])


def search(key: str, text: str, max_results: int = PAGE_SIZE, session=None):
    """Raw Places results for one query, in Google's ranking order."""
    session = session or requests
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS}
    body = {"textQuery": text, "regionCode": "gb", "languageCode": "en", "pageSize": PAGE_SIZE}
    out = []
    while len(out) < max_results:
        for attempt in range(3):
            r = session.post(API, json=body, headers=headers, timeout=30)
            if r.status_code in (429, 500, 503):
                time.sleep(5 * (attempt + 1))
                continue
            break
        if r.status_code >= 400:
            raise RuntimeError(f"Places API error {r.status_code}: {r.text[:300]}")
        data = r.json()
        out.extend(data.get("places") or [])
        token = data.get("nextPageToken")
        if not token:
            break
        body = {**body, "pageToken": token}
    return out[:max_results]


def _component(place, kind):
    for c in place.get("addressComponents") or []:
        if kind in (c.get("types") or []):
            return c.get("longText") or ""
    return ""


def to_item(place, search_string: str, rank: int) -> dict:
    """Places result -> the Apify Google Maps item shape used by maps.to_prospect."""
    status = place.get("businessStatus") or "OPERATIONAL"
    types = [t.replace("_", " ") for t in place.get("types") or []]
    return {
        "title": (place.get("displayName") or {}).get("text") or "",
        "website": place.get("websiteUri") or "",
        "phone": place.get("nationalPhoneNumber") or "",
        "city": _component(place, "postal_town") or _component(place, "locality"),
        "postalCode": _component(place, "postal_code"),
        "totalScore": place.get("rating") or "",
        "reviewsCount": place.get("userRatingCount") or "",
        "categoryName": (place.get("primaryTypeDisplayName") or {}).get("text") or "",
        "categories": types,
        "permanentlyClosed": status == "CLOSED_PERMANENTLY",
        "temporarilyClosed": status == "CLOSED_TEMPORARILY",
        "searchString": search_string,
        "rank": rank,
        # Google already matched these to "kitchen showroom", and its own
        # categories are too vague ("General contractor") to re-check.
        "_trusted": True,
    }


def run_search(key: str, queries, max_per_query: int = PAGE_SIZE, session=None):
    """Search every query and return items in the Apify Maps shape."""
    items = []
    for q in queries:
        for rank, place in enumerate(search(key, q, max_per_query, session), 1):
            items.append(to_item(place, q, rank))
    return items
