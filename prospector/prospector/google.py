"""Stage 3: where does the showroom appear when someone searches
"kitchen showroom [town]" on Google?

One search per town covers every showroom in that town. The organic top 10
and ads come from the Apify Google Search scraper. That scraper doesn't list
the businesses in the map pack, so the map pack is the top 3 from Google's
Places search for the same query.

Running Google Ads = Y only when the showroom has an ad on this search. A
showroom could be advertising on other searches; this measures the search a
customer in their town would actually type.
"""

import re
from urllib.parse import quote_plus

from . import places
from .apify import run_actor
from .maps import QUERY
from .matching import domain, name_similarity

SEARCH_ACTOR = "apify/google-search-scraper"
PACK_SIZE = 3

# Results that are directories or platforms, not a competing showroom.
_DIRECTORIES = (
    "google.", "checkatrade.", "yell.", "houzz.", "facebook.", "instagram.", "trustatrader.",
    "mybuilder.", "bark.", "ratedpeople.", "yelp.", "tripadvisor.", "wikipedia.", "youtube.",
    "pinterest.", "freeindex.", "thomsonlocal.", "cylex", "scoot.", "kbsa.", "which.co.uk",
    "trustpilot.", "192.com", "tiktok.", "linkedin.", "nextdoor.", "reddit.", "gumtree.",
)


def query(town: str) -> str:
    return QUERY.format(town=town)


def search_url(town: str) -> str:
    return "https://www.google.co.uk/search?q=" + quote_plus(query(town)) + "&gl=uk&hl=en"


def run_searches(token: str, towns):
    """{town: search result item}"""
    towns = sorted(set(towns))
    if not towns:
        return {}
    items = run_actor(token, SEARCH_ACTOR, {
        "queries": "\n".join(search_url(t) for t in towns),
        "resultsPerPage": 10,
        "maxPagesPerQuery": 1,
        "mobileResults": False,
    })
    by_query = {query(t).lower(): t for t in towns}
    out = {}
    for it in items:
        term = ((it.get("searchQuery") or {}).get("term") or "").lower()
        if term in by_query:
            out[by_query[term]] = it
    return out


def run_map_packs(places_key: str, towns):
    """{town: [top 3 places]} from Google's Places search for the same query."""
    towns = sorted(set(towns))
    items = places.run_search(places_key, [query(t) for t in towns], PACK_SIZE) if towns else []
    return map_packs_from_items(items, towns)


def map_packs_from_items(items, towns):
    by_query = {query(t).lower(): t for t in towns}
    grouped = {}
    for i, it in enumerate(items):
        town = by_query.get((it.get("searchString") or "").lower())
        if town:
            grouped.setdefault(town, []).append((it.get("rank") or i + 1000, it))
    return {t: [it for _, it in sorted(g, key=lambda x: x[0])[:PACK_SIZE]] for t, g in grouped.items()}


def _is_directory(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in _DIRECTORIES)


def _organic(search_item):
    out = []
    for r in search_item.get("organicResults") or []:
        url = r.get("url") or ""
        if re.search(r"//(www\.)?google\.", url):  # the "Map" placeholder
            continue
        if (r.get("position") or 0) <= 10:
            out.append(r)
    return out


def _is_them(company, website, name="", url=""):
    site = domain(website)
    if site and domain(url) == site:
        return True
    return bool(name) and name_similarity(name, company) >= 0.85


def _short_title(title: str) -> str:
    return re.split(r"\s[|\-–:]\s", title or "")[0].strip()


def result_for(company: str, website: str, search_item, pack) -> dict:
    """Values for Running Google Ads, Result for kitchen showroom [town],
    and Competitor ranking instead."""
    search_item = search_item or {}
    paid = search_item.get("paidResults") or []
    organic = _organic(search_item)
    pack = pack or []

    in_paid = any(_is_them(company, website, url=p.get("url") or p.get("displayedUrl") or "") for p in paid)
    in_pack = any(_is_them(company, website, p.get("title") or "", p.get("website") or "") for p in pack)
    in_organic = any(_is_them(company, website, url=r.get("url") or "") for r in organic)

    if in_paid:
        where = "Google Ads"
    elif in_pack:
        where = "Map pack"
    elif in_organic:
        where = "Organic top 10"
    else:
        where = "Not found"

    # Who a customer sees first instead: the top map pack showroom, else the
    # top organic result that isn't a directory.
    competitor = ""
    for p in pack:
        if not _is_them(company, website, p.get("title") or "", p.get("website") or ""):
            competitor = (p.get("title") or "").strip()
            break
    if not competitor:
        for r in organic:
            if not _is_directory(r.get("url")) and not _is_them(company, website, url=r.get("url") or ""):
                competitor = _short_title(r.get("title"))
                break

    return {
        "running_google_ads": "Y" if in_paid else "N",
        "search_result": where,
        "competitor_ranking": competitor,
    }
