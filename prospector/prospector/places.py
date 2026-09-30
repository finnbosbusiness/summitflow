"""Stage 1 (and the map pack for stage 3): find kitchen showrooms with
Google's Places API (Text Search).

Google gives a monthly free allowance for Text Search, so a search per town
costs little or nothing at this volume. Each result is converted to the same
shape the rest of the pipeline already reads (the Apify Google Maps format).
"""

import time

import requests

API = "https://places.googleapis.com/v1/places:searchText"
PAGE_SIZE = 20  # Google's maximum per page; it returns at most 3 pages
UK_BOUNDS = {"low": {"latitude": 49.8, "longitude": -8.7}, "high": {"latitude": 60.9, "longitude": 1.8}}
FIELDS = ",".join([
    "places.id", "places.displayName", "places.addressComponents", "places.websiteUri",
    "places.nationalPhoneNumber", "places.rating", "places.userRatingCount", "places.types",
    "places.primaryTypeDisplayName", "places.businessStatus", "places.formattedAddress",
    "places.googleMapsUri", "nextPageToken",
])
SEARCH_CALLS = [0]  # billed Text Search requests this run


def search(key: str, text: str, max_results: int = PAGE_SIZE, session=None, keep_paging=None):
    """Raw Places results for one query, in Google's ranking order.

    After each page, keep_paging(page) decides whether the next page is worth
    fetching (each page is a billed request), so paging stops once results
    stop being relevant.
    """
    session = session or requests
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS}
    body = {"textQuery": text, "regionCode": "gb", "languageCode": "en", "pageSize": PAGE_SIZE,
            # Keep results in the UK: "Warwick" or "Leominster" otherwise
            # also returns showrooms in the US towns of the same name.
            "locationRestriction": {"rectangle": UK_BOUNDS}}
    out = []
    while len(out) < max_results:
        for attempt in range(4):
            r = session.post(API, json=body, headers=headers, timeout=30)
            if r.status_code in (429, 500, 503):
                time.sleep(5 * (attempt + 1))
                continue
            break
        if r.status_code >= 400:
            raise RuntimeError(f"Places API error {r.status_code}: {r.text[:300]}")
        data = r.json()
        page = data.get("places") or []
        out.extend(page)
        SEARCH_CALLS[0] += 1
        token = data.get("nextPageToken")
        if not token or (keep_paging and not keep_paging(page)):
            break
        body = {**body, "pageToken": token}
    return out[:max_results]


def _component(place, kind, short=False):
    for c in place.get("addressComponents") or []:
        if kind in (c.get("types") or []):
            return (c.get("shortText") if short else c.get("longText")) or ""
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
        "countryCode": _component(place, "country", short=True),
        "address": place.get("formattedAddress") or "",
        # Drop the long tracking parameter Google appends to the link.
        "mapsUrl": (place.get("googleMapsUri") or "").split("&g_mp=")[0],
        "placeId": place.get("id") or "",
        # Google already matched these to "kitchen showroom", and its own
        # categories are too vague ("General contractor") to re-check.
        "_trusted": True,
    }


def run_search(key: str, queries, max_per_query: int = PAGE_SIZE, session=None, keep_paging=None, progress=None):
    """Search every query and return items in the Apify Maps shape.

    keep_paging(items) gets each page already converted to items.
    """
    items = []
    queries = list(queries)
    for i, q in enumerate(queries, 1):
        page_filter = (lambda page, q=q: keep_paging([to_item(p, q, 0) for p in page])) if keep_paging else None
        for rank, place in enumerate(search(key, q, max_per_query, session, page_filter), 1):
            items.append(to_item(place, q, rank))
        if progress and i % 50 == 0:
            progress(f"  {i}/{len(queries)} towns searched ({SEARCH_CALLS[0]} Places requests)")
    return items
