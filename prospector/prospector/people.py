"""Stage 5: find a decision maker for every showroom without one.

For each row with no Decision maker name, in this order:

1. Its website's home page, About / Meet the team / Our story / Contact
   pages. If any page gives a company registration number ("Registered in
   England No. 01234567"), that company's longest-serving director on
   Companies House is used: the most reliable source.
2. Otherwise a name the site gives next to Owner, Founder, Managing
   Director, Director or Proprietor ("Dave Smith, Founder", "Owner: Dave
   Smith", "Hi, I'm Dave, the owner").
3. Otherwise (with --facebook, paid via Apify) the same rules on the
   Facebook page the website links to.

Email is filled in too when it's blank: the first address on the
showroom's own domain, else the first address on its site.

    python -m prospector.people --dry-run --limit 20
    python -m prospector.people              # every row without a name
    python -m prospector.people --facebook   # also try Facebook pages
"""

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import html
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from .matching import domain

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}
MAX_BYTES = 600_000
MAX_SUBPAGES = 4
FACEBOOK_ACTOR = "apify/facebook-pages-scraper"

# Pages worth reading, best first.
_PAGE_WORDS = ("meet-the-team", "meet the team", "our-team", "our team", "the-team", "team", "about", "our-story",
               "our story", "who-we-are", "who we are", "people", "founder", "history", "contact")

_ROLES = [
    ("Managing Director", r"managing\s+director"),
    ("Co-founder", r"co-?\s?founder"),
    ("Founder", r"founder"),
    ("Owner", r"(?:business\s+)?owner"),
    ("Proprietor", r"proprietor"),
    ("Director", r"(?:company\s+)?director"),
    ("Managing Director", r"\bMD\b"),
]
# The sheet's Decision maker role dropdown: Owner, Founder, Managing Director,
# Director, Marketing Director, General Manager, Other.
_DROPDOWN = {"Co-founder": "Founder", "Proprietor": "Owner"}
_ROLE_RANK = {"Owner": 0, "Founder": 0, "Co-founder": 1, "Managing Director": 1, "Proprietor": 1, "Director": 2}

_NAME_WORD = r"(?:O'|Mc|Mac)?[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
# Names stay on one line: "Meet the team\nDave Smith" must not become one name.
_FULL_NAME = rf"{_NAME_WORD}(?:[ \t]+{_NAME_WORD}){{1,2}}"
_ANY_NAME = rf"{_NAME_WORD}(?:[ \t]+{_NAME_WORD}){{0,2}}"

# Capitalised words that turn up next to "Director" or "Owner" but aren't names.
_NOT_NAME = set("""
about our the and meet team showroom showrooms kitchen kitchens bathroom bathrooms bedroom bedrooms design designs
designer designers interiors interior home homes contact us call email phone view read more our story company ltd
limited director directors managing founder founders owner owners proprietor sales manager group studio furniture
bespoke luxury quality service services welcome hello hi dear family business uk england london head office
installation installer fitter fitters project projects customer customers client clients new news blog gallery
privacy policy terms cookies copyright all rights reserved registered number vat mr mrs ms miss dr sir
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august
september october november december who we are what why how where when this that with from your you
message chief executive officer ceo md co operations finance marketing creative technical senior lead
by behind final step first last next north south east west yorkshire lancashire cheshire kent surrey essex
sussex devon cornwall norfolk suffolk county
roof roofs roofing roofer roofers roofline construction building builders specialist specialists area areas
quote quotes free follow should must know trusted businesses occupier jobs led farm meadow local near best
""".split())


def _towns():
    import csv
    from pathlib import Path
    try:
        with open(Path(__file__).resolve().parent.parent / "towns.csv", newline="") as f:
            return {r["town"].strip().lower() for r in csv.DictReader(f)}
    except OSError:
        return set()


_TOWN_NAMES = _towns()

_COMPANY_NO = re.compile(
    r"(?:company|registration|registered|reg\.?|co\.?)\s*(?:in\s+england(?:\s*(?:&|and)\s*wales)?\s*)?"
    r"(?:no\.?|number|num\.?|#)?\s*[:.\-]?\s*((?:SC|NI|OC)?\s?\d{6,8})\b",
    re.I,
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BAD_EMAIL = ("example.", "sentry", "wixpress", "domain.com", "email.com", "yourname", ".png", ".jpg", ".webp",
              "godaddy", "filler@", "yourdomain", "test@", "user@", "name@", "wordpress", "squarespace")


class _Page(HTMLParser):
    """Visible text (one line per block) and links of an HTML page."""

    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "section", "article",
              "header", "footer", "span", "figcaption", "blockquote", "strong", "b", "em"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.scripts = [], [], []
        self._skip = 0
        self._ld = False
        self._href = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
            self._ld = tag == "script" and (a.get("type") or "").lower() == "application/ld+json"
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href = a.get("href") or ""
            self._link_text = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip = max(0, self._skip - 1)
            self._ld = False
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._link_text).strip()))
            self._href = None

    def handle_data(self, data):
        if self._ld:
            self.scripts.append(data)
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._link_text.append(data.strip())

    @property
    def text(self):
        lines = (" ".join(line.split()) for line in "".join(self.parts).split("\n"))
        return "\n".join(line for line in lines if line)


def parse(html_text):
    p = _Page()
    try:
        p.feed(html_text or "")
    except Exception:  # broken markup: keep what was parsed
        pass
    return p


def _is_name(name, company=""):
    words = name.split()
    if not words or len(name) > 40 or name.lower() in _TOWN_NAMES:
        return False
    if any(w.lower().strip("'.") in _NOT_NAME for w in words):
        return False
    # "Margan Roofing" on SB Margan Roofing's site is the business, not a person.
    company_words = set(re.findall(r"[a-z]+", company.lower()))
    return not (company_words and all(w.lower().strip("'.") in company_words for w in words))


def _clean(name):
    return " ".join(w.capitalize() if w.isupper() else w for w in name.split())


def find_people(text):
    """[(name, role)] from a page's visible text, best first."""
    found = []
    for role, rx in _ROLES:
        # Role words match any case; names must start with capitals.
        rx = rx if rx == r"\bMD\b" else f"(?i:{rx})"
        patterns = [
            # "Dave Smith, Founder" / "Dave Smith - Managing Director" / "Dave Smith\nOwner"
            rf"({_FULL_NAME})[ \t]*(?:[,\-–—|:(]|\n)\s*(?i:and\s+|our\s+|the\s+)?(?:{rx})\b",
            # "Founder: Dave Smith" / "Owner Dave Smith". Same line only: in a
            # team list the role under one name isn't the next person's.
            rf"(?:{rx})(?:[ \t]*(?:&|and)[ \t]*[a-z-]+)?[ \t]*[:,\-–—|]?[ \t]*({_FULL_NAME})\b",
            # "Dave, the owner" / "I'm Dave, owner of"
            rf"\b(?i:I'm|I am|my name is|name's)\s+({_ANY_NAME})\s*,?\s*(?i:and\s+I'm\s+)?(?i:the\s+|your\s+)?(?:{rx})\b",
            rf"({_ANY_NAME})\s*,\s*(?i:the\s+|our\s+)?(?:{rx})\s+(?i:of|at|and)\b",
        ]
        for pat in patterns:
            for m in re.finditer(pat, text):
                name = _clean(m.group(1).strip())
                if _is_name(name):
                    found.append((name, role))
    seen, out = set(), []
    for name, role in sorted(found, key=lambda nr: (_ROLE_RANK.get(nr[1], 3), -len(nr[0].split()))):
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append((name, role))
    return out


def people_from_json_ld(scripts):
    out = []
    for raw in scripts:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                for key, role in (("founder", "Founder"), ("founders", "Founder")):
                    v = d.get(key)
                    for p in (v if isinstance(v, list) else [v]):
                        name = p.get("name") if isinstance(p, dict) else p
                        if isinstance(name, str) and len(name.split()) >= 2 and _is_name(name):
                            out.append((_clean(name.strip()), role))
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
    return out


def company_numbers(text):
    out = []
    for m in _COMPANY_NO.finditer(text):
        n = m.group(1).replace(" ", "").upper()
        if n[:2].isalpha():
            n = n[:2] + n[2:].zfill(6)
        else:
            n = n.zfill(8)
        if n not in out and n != "00000000":
            out.append(n)
    return out


def emails(text, own_domain):
    found = []
    for e in _EMAIL.findall(text):
        e = e.strip(".").lower()
        if any(b in e for b in _BAD_EMAIL) or e in found:
            continue
        found.append(e)
    own = [e for e in found if own_domain and e.split("@", 1)[1].endswith(own_domain)]
    return own + [e for e in found if e not in own]


def facebook_url(links):
    for href, _ in links:
        u = urlparse(href)
        host = (u.hostname or "").lower()
        if host.endswith("facebook.com") and u.path.strip("/") and not any(
                s in u.path for s in ("sharer", "share.php", "/plugins", "/dialog", "/tr")):
            return f"https://www.facebook.com{u.path.rstrip('/')}"
    return ""


def subpages(base_url, links):
    """Same-site links worth reading (about, team, contact), best first."""
    host = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    scored = {}
    for href, text in links:
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        u = urlparse(url)
        if (u.hostname or "").lower().removeprefix("www.") != host or u.path.lower().endswith(
                (".pdf", ".jpg", ".png", ".jpeg", ".webp", ".zip")):
            continue
        hay = f"{u.path} {text}".lower()
        for rank, word in enumerate(_PAGE_WORDS):
            if word in hay:
                scored[url] = min(scored.get(url, 99), rank)
                break
    return [u for u, _ in sorted(scored.items(), key=lambda kv: kv[1])][:MAX_SUBPAGES]


def fetch(session, url):
    try:
        r = session.get(url, headers=HEADERS, timeout=15, allow_redirects=True, stream=True)
        if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", "html"):
            return None, url
        body = r.raw.read(MAX_BYTES, decode_content=True)
        return body.decode(r.encoding or "utf-8", errors="replace"), r.url
    except Exception:
        return None, url


def scan_website(website, session=None):
    """Read a showroom's site. Returns {'people', 'company_numbers', 'emails',
    'facebook', 'pages'}."""
    session = session or requests.Session()
    out = {"people": [], "company_numbers": [], "emails": [], "facebook": "", "pages": 0}
    body, final = fetch(session, website)
    if body is None:
        return out
    pages = [(final, parse(body))]
    for url in subpages(final, pages[0][1].links):
        b, u = fetch(session, url)
        if b is not None:
            pages.append((u, parse(b)))
    own = domain(final) or domain(website)
    for url, page in pages:
        text = html.unescape(page.text)
        out["people"] += people_from_json_ld(page.scripts) + find_people(text)
        out["company_numbers"] += [n for n in company_numbers(text) if n not in out["company_numbers"]]
        mailtos = " ".join(h[7:].split("?")[0] for h, _ in page.links if h.lower().startswith("mailto:"))
        out["emails"] += [e for e in emails(mailtos + "\n" + text, own) if e not in out["emails"]]
        out["facebook"] = out["facebook"] or facebook_url(page.links)
    out["pages"] = len(pages)
    # Best role first; keep the first time each name was seen.
    seen, people = set(), []
    for name, role in sorted(out["people"], key=lambda nr: _ROLE_RANK.get(nr[1], 3)):
        if name.lower() not in seen:
            seen.add(name.lower())
            people.append((name, role))
    out["people"] = people
    return out


def decide(scan, companies_house=None, company=""):
    """Turn a website scan into sheet fields for one row."""
    fields = {}
    scan = {**scan, "people": [(n, r) for n, r in scan["people"] if _is_name(n, company)]}
    for number in scan["company_numbers"]:
        if not companies_house:
            break
        info = companies_house.by_number(number)
        if info.get("decision_maker_name"):
            fields.update(info)
            fields["decision_maker_role"] = "Director"
            break
    if not fields.get("decision_maker_name") and scan["people"]:
        name, role = scan["people"][0]
        fields["decision_maker_name"] = name
        fields["decision_maker_role"] = _DROPDOWN.get(role, role)
    if scan["emails"]:
        fields["email"] = scan["emails"][0]
    return fields


def facebook_people(token, urls):
    """{facebook url: [(name, role)]} from the Apify Facebook pages scraper."""
    from .apify import run_actor

    if not urls:
        return {}
    items = run_actor(token, FACEBOOK_ACTOR, {"startUrls": [{"url": u} for u in urls]})
    out = {}
    for it in items:
        url = (it.get("facebookUrl") or it.get("pageUrl") or it.get("url") or "").rstrip("/")
        text = "\n".join(str(x) for x in [it.get("title"), it.get("intro"), it.get("about_me"),
                                          *(it.get("info") or [])] if x)
        key = next((u for u in urls if u.rstrip("/").lower() == url.lower()), url)
        out[key] = find_people(text)
    return out


def main(argv=None):
    from .companies_house import CompaniesHouse
    from .run import DEFAULT_SHEET_ID, OUTPUT, sheet_id_from
    from .sheet import Plan, configure, open_sheet, read_prospects, tidy, write

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--niche", default="kitchen", choices=["kitchen", "roofing", "bathroom"],
                    help="which trade's tab")
    ap.add_argument("--limit", type=int, help="only the first N rows without a name (for a test)")
    ap.add_argument("--facebook", action="store_true", help="also check Facebook pages via Apify (paid)")
    ap.add_argument("--workers", type=int, default=8, help="websites read at once")
    ap.add_argument("--dry-run", action="store_true", help="write a CSV to output/ instead of the sheet")
    args = ap.parse_args(argv)

    configure(args.niche)
    spreadsheet = open_sheet(sheet_id_from(os.environ.get("SHEET_ID") or DEFAULT_SHEET_ID))
    if not args.dry_run:
        towns, cleared = tidy(spreadsheet, Plan(read_prospects(spreadsheet)))
        print(f"Tidy: {towns} town names fixed, {cleared} untrustworthy Companies House matches cleared, "
              "sorted by town")
    plan = Plan(read_prospects(spreadsheet))
    todo = [r for r, v in sorted(plan.rows.items())
            if str(v.get("company", "")).strip() and not str(v.get("decision_maker_name", "")).strip()]
    no_site = [r for r in todo if not domain(plan.rows[r].get("website", ""))]
    todo = [r for r in todo if r not in no_site]
    print(f"{len(todo) + len(no_site)} showrooms without a decision maker: {len(todo)} have a website, "
          f"{len(no_site)} don't")
    if args.limit:
        todo = todo[: args.limit]
        print(f"  checking the first {len(todo)} (--limit)")

    key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    ch = CompaniesHouse(key) if key else None
    if not ch:
        print("  COMPANIES_HOUSE_API_KEY not set: company numbers found on websites won't be looked up")

    scans = {}
    with cf.ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(scan_website, plan.rows[r]["website"]): r for r in todo}
        for i, f in enumerate(cf.as_completed(futures), 1):
            scans[futures[f]] = f.result()
            if i % 100 == 0:
                print(f"  websites read: {i}/{len(todo)}")

    found, sources = 0, {"Companies House number on website": 0, "named on website": 0}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    # A name found on three or more different showrooms' sites is template
    # filler (a stock testimonial or "about us" page), not their owner.
    seen = {}
    for r in todo:
        for name, _ in scans[r]["people"][:1]:
            seen.setdefault(name.lower(), set()).add(domain(plan.rows[r]["website"]))
    template = {n for n, sites in seen.items() if len(sites) >= 3}
    if template:
        print(f"  ignoring names used on 3+ sites: {', '.join(sorted(template))}")
    for r in todo:
        scans[r]["people"] = [(n, role) for n, role in scans[r]["people"] if n.lower() not in template]

    for i, r in enumerate(todo, 1):
        fields = decide(scans[r], ch, plan.rows[r]["company"])
        if fields.get("decision_maker_name"):
            found += 1
            sources["Companies House number on website" if fields.get("company_number") else "named on website"] += 1
        # Company details found via the website replace a "Not found".
        if fields.get("company_number") and plan.rows[r].get("company_status") in ("", "Not found"):
            plan.set(r, {k: fields.pop(k) for k in ("company_number", "company_status", "incorporated") if k in fields})
        plan.fill(r, fields)
        if not args.dry_run and i % 200 == 0:
            write(spreadsheet, plan)
            print(f"  saved {i}/{len(todo)}")
    print(f"Website: found a decision maker for {found} of {len(todo)} "
          + "(" + ", ".join(f"{n} {s}" for s, n in sorted(sources.items(), key=lambda kv: -kv[1])) + ")")

    if args.facebook:
        left = {r: scans[r]["facebook"] for r in todo
                if not plan.rows[r].get("decision_maker_name") and scans[r]["facebook"]}
        token = os.environ.get("APIFY_TOKEN") or sys.exit("APIFY_TOKEN is not set (needed for --facebook).")
        print(f"Facebook: checking {len(left)} pages")
        try:
            fb = facebook_people(token, sorted(set(left.values())))
        except Exception as e:  # keep what the website pass found
            fb = {}
            print(f"Facebook: FAILED ({e})")
        n = 0
        for r, url in left.items():
            people = fb.get(url) or []
            if people:
                n += 1
                plan.fill(r, {"decision_maker_name": people[0][0],
                              "decision_maker_role": _DROPDOWN.get(people[0][1], people[0][1])})
        print(f"Facebook: found a decision maker for {n} of {len(left)}")

    if args.dry_run:
        OUTPUT.mkdir(exist_ok=True)
        out = OUTPUT / f"people-{stamp}.csv"
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sheet_row", "company", "website", "decision_maker_name", "decision_maker_role", "email",
                        "company_number", "facebook"])
            for r in todo:
                v = plan.rows[r]
                w.writerow([r, v["company"], v["website"], v["decision_maker_name"], v["decision_maker_role"],
                            v["email"], v["company_number"], scans[r]["facebook"]])
        print(f"Dry run: written to {out}")
    else:
        write(spreadsheet, plan)
        print("Saved to sheet")


if __name__ == "__main__":
    main()
