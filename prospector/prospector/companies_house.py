"""Stage 4: company number, status, incorporation date and a decision maker
from the Companies House public data API (free key from
https://developer.company-information.service.gov.uk/)."""

import time

import requests

from .matching import name_similarity, outward_postcode

API = "https://api.company-information.service.gov.uk"

_STATUS = {
    "active": "Active",
    "liquidation": "Liquidation",
    "administration": "Liquidation",
    "insolvency-proceedings": "Liquidation",
    "receivership": "Liquidation",
    "voluntary-arrangement": "Active",
}
# Company number prefixes for Scottish and Northern Irish registrations.
_NOT_ENGLAND_WALES = ("SC", "SO", "SL", "NI", "NC", "NL", "R0")


class CompaniesHouse:
    def __init__(self, api_key: str, min_interval_s: float = 0.55):
        self.session = requests.Session()
        self.session.auth = (api_key, "")
        # Free tier allows 600 requests per 5 minutes; stay just under it.
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def _get(self, path, **params):
        wait = self.min_interval_s - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            self._last = time.time()
            r = self.session.get(API + path, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(60 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise RuntimeError("Companies House rate limit: gave up after retries")

    def lookup(self, name: str, postcode: str) -> dict:
        """Best-effort match. Returns only the fields it is confident about."""
        data = self._get("/search/companies", q=name, items_per_page=10) or {}
        match = best_match(name, postcode, data.get("items") or [])
        if not match:
            return {"company_status": "Not found"}

        out = {
            "company_number": match["company_number"],
            "company_status": _STATUS[match["company_status"]],
            "incorporated": match.get("date_of_creation", ""),
        }
        officers = self._get(f"/company/{match['company_number']}/officers", items_per_page=50) or {}
        director = pick_director(officers.get("items") or [])
        if director:
            out["decision_maker_name"] = director
            out["decision_maker_role"] = "Director"
        return out


def best_match(name, postcode, candidates):
    """Pick the search result that is really this showroom.

    Accept a very close name match on its own, or a reasonable name match
    whose registered address shares the outward postcode (e.g. CV34).

    Dissolved companies are never matched: the showroom is open on Google
    Maps, so a dissolved company with the same name is an old or unrelated
    business, not this one. Scottish and Northern Irish registrations are
    skipped for the same reason, since every prospect is in England.
    """
    area = outward_postcode(postcode)
    best, best_score = None, 0.0
    for c in candidates:
        if c.get("company_status") not in _STATUS:
            continue
        if (c.get("company_number") or "").upper().startswith(_NOT_ENGLAND_WALES):
            continue
        if c.get("company_type") in ("ltd", "llp", "private-limited-guarant-nsc", "plc") or not c.get("company_type"):
            sim = name_similarity(name, c.get("title", ""))
            same_area = bool(area) and area in (c.get("address_snippet") or "").upper()
            if sim >= 0.9 or (sim >= 0.6 and same_area):
                score = sim + (0.2 if same_area else 0) + (0.1 if c.get("company_status") == "active" else 0)
                if score > best_score:
                    best, best_score = c, score
    return best


def pick_director(officers):
    """Longest-serving current director, as 'First Last'."""
    current = [
        o for o in officers
        if not o.get("resigned_on") and o.get("officer_role") in ("director", "llp-designated-member", "llp-member")
    ]
    if not current:
        return ""
    current.sort(key=lambda o: o.get("appointed_on") or "9999")
    return format_officer_name(current[0].get("name", ""))


def format_officer_name(raw: str) -> str:
    """'SMITH, John Paul' -> 'John Paul Smith'. Corporate officers pass through."""
    if "," not in raw:
        return raw.strip(" ,")
    surname, forenames = (p.strip(" ,") for p in raw.split(",", 1))
    # Drop titles such as 'Mr' that Companies House sometimes leaves in.
    words = [w for w in forenames.split() if w.lower().rstrip(".") not in ("mr", "mrs", "ms", "miss", "dr")]
    return " ".join(words + [surname.title()])
