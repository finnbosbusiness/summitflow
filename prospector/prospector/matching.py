"""Helpers for telling showrooms apart: website domains and company names."""

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

# Hosts that are not the showroom's own website.
_NOT_OWN_SITE = (
    "facebook.com", "instagram.com", "linktr.ee", "wixsite.com", "business.site",
    "google.com", "yell.com", "checkatrade.com", "houzz.co.uk", "houzz.com",
)

_COMPANY_SUFFIXES = r"\b(ltd|limited|llp|plc|uk|co|company|the|and)\b"


def domain(url: str) -> str:
    """Normalise a URL to its bare domain: 'https://www.Foo.co.uk/x' -> 'foo.co.uk'.

    Returns '' for empty input or for social/directory hosts.
    """
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if any(host == h or host.endswith("." + h) for h in _NOT_OWN_SITE):
        return ""
    return host


def clean_name(name: str) -> str:
    name = (name or "").lower().replace("&", " and ")
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(_COMPANY_SUFFIXES, " ", name)
    return " ".join(name.split())


def name_similarity(a: str, b: str) -> float:
    a, b = clean_name(a), clean_name(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def outward_postcode(postcode: str) -> str:
    """'CV34 4AB' -> 'CV34'."""
    parts = (postcode or "").upper().split()
    return parts[0] if parts else ""


def prospect_key(website: str, name: str, postcode: str) -> str:
    """Stable identity for a showroom. Website domain when there is one,
    otherwise cleaned name plus outward postcode."""
    d = domain(website)
    if d:
        return "d:" + d
    return "n:" + clean_name(name) + "|" + outward_postcode(postcode)
