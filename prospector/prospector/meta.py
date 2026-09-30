"""Stage 2: is the showroom running Meta (Facebook/Instagram) ads, since when,
and what does the ad say? Uses the Apify Facebook Ad Library scraper.

The Ad Library keyword search is loose (searching "Madina Kitchens" also
returns other advertisers' ads), so an ad only counts when it links to the
showroom's own website or comes from a Facebook page with the showroom's name.
"""

import datetime as dt
import re
from urllib.parse import quote_plus

from .apify import run_actor
from .matching import clean_name, domain, name_similarity

ACTOR = "apify/facebook-ads-scraper"
LIBRARY = (
    "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=GB"
    "&search_type=keyword_unordered&media_type=all&q={q}"
)
PAGE_NAME_MATCH = 0.85


def search_name(company: str) -> str:
    """'Avanti | Kitchens, Bathrooms ... | Solihull' -> 'Avanti';
    'Madina Kitchens Ltd' -> 'Madina Kitchens'."""
    name = re.split(r"\s[|\-–]\s|\s\(", company or "")[0]
    name = re.sub(r"\b(ltd|limited|llp|plc)\b\.?", "", name, flags=re.I)
    return " ".join(name.split())


def library_url(company: str) -> str:
    return LIBRARY.format(q=quote_plus(search_name(company)))


def run_search(token: str, companies, per_search: int):
    urls = sorted({library_url(c) for c in companies if search_name(c)})
    if not urls:
        return []
    return run_actor(token, ACTOR, {
        "startUrls": [{"url": u} for u in urls],
        "resultsLimit": per_search,
        "activeStatus": "active",
    })


def _snapshot(ad):
    return ad.get("snapshot") or {}


def ad_domains(ad):
    snap = _snapshot(ad)
    out = {domain(snap.get("linkUrl") or ""), domain(snap.get("caption") or "")}
    for card in snap.get("cards") or []:
        out.add(domain(card.get("linkUrl") or ""))
    out.discard("")
    return out


def ad_page_name(ad):
    return (ad.get("pageName") or _snapshot(ad).get("pageName") or "").strip()


def is_match(ad, company: str, website: str) -> bool:
    site = domain(website)
    if site and site in ad_domains(ad):
        return True
    page = ad_page_name(ad)
    if not page:
        return False
    if clean_name(page) == clean_name(search_name(company)):
        return True
    return name_similarity(page, search_name(company)) >= PAGE_NAME_MATCH


def _start(ad):
    ts = ad.get("startDate")
    if isinstance(ts, (int, float)) and ts > 0:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
    s = ad.get("startDateFormatted") or ""
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def hook(ad, limit: int = 140) -> str:
    """First sentence or line of the ad text, skipping template placeholders."""
    snap = _snapshot(ad)
    text = ((snap.get("body") or {}).get("text") or "").strip()
    if not text or "{{" in text:
        text = (snap.get("title") or "").strip()
    if "{{" in text:
        return ""
    first = re.split(r"(?<=[.!?])\s+|\n", text, maxsplit=1)[0].strip()
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def result_for(company: str, website: str, ads) -> dict:
    """Values for Running Meta ads, Oldest live ad start and Meta ad hook."""
    mine = [a for a in ads if a.get("isActive", True) and is_match(a, company, website)]
    if not mine:
        return {"running_meta_ads": "N", "oldest_ad_start": "", "meta_ad_hook": ""}
    dated = [(d, a) for a in mine if (d := _start(a))]
    oldest_date, oldest_ad = min(dated, key=lambda t: t[0]) if dated else (None, mine[0])
    return {
        "running_meta_ads": "Y",
        "oldest_ad_start": oldest_date.isoformat() if oldest_date else "",
        # The longest-running ad is the one that's working for them.
        "meta_ad_hook": hook(oldest_ad),
    }
