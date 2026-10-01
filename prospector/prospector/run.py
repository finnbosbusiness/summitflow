"""Prospect run: Google Places -> Companies House -> Prospects tab
(optionally the Google search and Meta ad checks).

    python -m prospector.run --region "West Midlands" --dry-run
    python -m prospector.run --region England --skip-google --skip-meta
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from . import google, location, maps, meta, places
from .chains import FALLBACK_CHAINS, is_chain
from .columns import FIRST_ROW, INPUT_COLUMNS, LAST_ROW
from .companies_house import CompaniesHouse
from .matching import domain
from . import sheet
from .sheet import Plan, keys_for, open_sheet, read_chains, read_prospects, write, write_skipped

# Only showrooms in these countries are added.
COUNTRIES = {"England"}
DEFAULT_SHEET_ID = "1OB_KfC0N6aigLFe5-_y8D4_VFD8RW7zRnXEXCKN3ZYQ"
HERE = Path(__file__).resolve().parent.parent
OUTPUT = HERE / "output"


def sheet_id_from(value):
    """Accepts a sheet ID or a full sheet URL. Blank (e.g. an unset GitHub
    variable, which Actions passes as '') means the Cold Lead Template."""
    value = (value or "").strip()
    if not value:
        return DEFAULT_SHEET_ID
    m = re.search(r"/d/([A-Za-z0-9_-]+)", value)
    return m.group(1) if m else value


def load_towns(path, region=None):
    with open(path, newline="") as f:
        towns = {r["town"].strip(): r["region"].strip() for r in csv.DictReader(f) if r.get("town", "").strip()}
    if region and region.strip().lower() not in ("all", "england"):
        towns = {t: r for t, r in towns.items() if r.lower() == region.strip().lower()}
    return towns


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--niche", default="kitchen", choices=["kitchen", "roofing", "bathroom"],
                    help="which trade to search for; each has its own tab (see niches.py)")
    ap.add_argument("--towns", default=str(HERE / "towns.csv"), help="CSV with town,region columns")
    ap.add_argument("--region", help="only run towns in this region, e.g. 'West Midlands'")
    ap.add_argument("--only", help="comma-separated towns to run, e.g. 'Birmingham,Solihull'")
    ap.add_argument("--max-towns", type=int, help="only the first N towns (for a cheap test)")
    ap.add_argument("--max-per-town", type=int, default=60,
                    help="Google Places results per town (default 60, Google's maximum)")
    ap.add_argument("--min-relevant-per-page", type=int, default=8,
                    help="only fetch the next page of 20 if this many on the last page were kitchen businesses")
    ap.add_argument("--maps-json", help="reuse a saved Apify result instead of scraping again")
    ap.add_argument("--skip-companies-house", action="store_true")
    ap.add_argument("--skip-meta", action="store_true", help="skip stage 2 (Meta Ad Library)")
    ap.add_argument("--skip-google", action="store_true", help="skip stage 3 (Google search check)")
    ap.add_argument("--no-maps", action="store_true",
                    help="don't search for new showrooms; only fill in ad and search checks for rows already on the sheet")
    ap.add_argument("--ads-per-search", type=int, default=5, help="Meta ads fetched per showroom (default 5; you pay per ad)")
    ap.add_argument("--dry-run", action="store_true", help="write a CSV to output/ instead of the sheet")
    args = ap.parse_args(argv)

    niche = sheet.configure(args.niche)
    query = niche["query"]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    OUTPUT.mkdir(exist_ok=True)
    sheet_id = sheet_id_from(os.environ.get("SHEET_ID"))
    has_sheet = bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    if not args.dry_run and not has_sheet:
        sys.exit("No Google credentials set. Set GOOGLE_SERVICE_ACCOUNT_JSON, or use --dry-run.")

    towns = load_towns(args.towns, args.region)
    if args.only:
        wanted = {t.strip().lower() for t in args.only.split(",") if t.strip()}
        towns = {t: r for t, r in towns.items() if t.lower() in wanted}
    if args.max_towns:
        towns = dict(list(towns.items())[: args.max_towns])
    if args.no_maps:
        towns = {}
    elif not towns:
        sys.exit("No towns selected. Check --region matches the towns.csv spelling.")
    else:
        print(f"{len(towns)} towns: {', '.join(towns)}")

    # Existing sheet state
    spreadsheet = open_sheet(sheet_id) if has_sheet else None
    if isinstance(niche["chains"], list):
        chains = niche["chains"]
    else:
        chains = (read_chains(spreadsheet) if spreadsheet else FALLBACK_CHAINS) if niche["chains"] else []
    plan = Plan(read_prospects(spreadsheet) if spreadsheet else {})
    if not spreadsheet:
        plan.free = list(range(FIRST_ROW, sheet.ROWS[0] + 1))
    print(f"Sheet: {len(plan.index)} prospects already, {len(plan.free)} free rows, {len(chains)} chains excluded")

    # Stage 1: Google Maps
    token = os.environ.get("APIFY_TOKEN", "")
    places_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if args.no_maps:
        items = []
    elif args.maps_json:
        items = json.loads(Path(args.maps_json).read_text())
    else:
        places_key or sys.exit("GOOGLE_PLACES_API_KEY is not set.")

        def worth_next_page(page_items):
            relevant = sum(1 for it in page_items if maps.to_prospect(it, towns, args.niche, query)[0])
            return relevant >= args.min_relevant_per_page

        items = places.run_search(places_key, [query.format(town=t) for t in towns], args.max_per_town,
                                  keep_paging=worth_next_page, progress=print)
        raw = OUTPUT / f"maps-{stamp}.json"
        raw.write_text(json.dumps(items))
        print(f"Places: {places.SEARCH_CALLS[0]} requests. Saved raw results to {raw} (reuse with --maps-json)")

    counts = Counter()
    prospects, seen, skipped, skipped_seen = [], set(), [], set()

    def skip(item, reason):
        counts[reason] += 1
        # Record each skipped business once, so nothing is lost without a trace.
        ident = item.get("placeId") or (item.get("title"), item.get("postalCode"))
        if ident not in skipped_seen:
            skipped_seen.add(ident)
            skipped.append({
                "reason": reason, "company": item.get("title") or "", "website": item.get("website") or "",
                "phone": item.get("phone") or "", "postcode": item.get("postalCode") or "",
                "town": item.get("city") or "", "google_category": item.get("categoryName") or "",
                "maps_url": item.get("mapsUrl") or "", "searched": item.get("searchString") or "",
            })

    multi_site = maps.multi_site_domains(items)
    by_ident = {}
    for item in items:
        p, reason = maps.to_prospect(item, towns, args.niche, query)
        if not p:
            skip(item, reason)
            continue
        if domain(p["website"]) in multi_site:
            if args.niche != "roofing":
                skip(item, "multi-site")
                continue
            # Independent roofers often share a lead-generation site
            # (westmidlandsroofer.co.uk/roofer-in-bloxwich/), and regional
            # firms list each branch: keep them all, matched by name and
            # postcode instead of the shared website.
            p["website"] = ""
        if is_chain(p["company"], p["website"], chains):
            skip(item, "chain")
            continue
        # The same showroom often shows up in several towns' searches.
        ks = keys_for(p)
        if any(k in seen for k in ks):
            counts["duplicate"] += 1
            continue
        seen.update(ks)
        by_ident[id(p)] = item
        prospects.append(p)

    # Region from each showroom's own postcode, not from the search.
    postcode_info = location.lookup(p["postcode"] for p in prospects)
    kept = []
    for p in prospects:
        info = postcode_info.get(location.normalise(p["postcode"]))
        region = (info or {}).get("region") or location.region_from_area(p["postcode"]) or p["_searched_region"]
        if location.country_of_region(region) not in COUNTRIES:
            skip(by_ident[id(p)], "outside England")
            continue
        p["region"] = region
        kept.append(p)
    # New rows go onto the sheet grouped by town, in postcode order within it.
    prospects = sorted(kept, key=lambda p: (p["town"].lower(), location.sort_key(p["postcode"])))

    if not args.no_maps:
        print(f"Maps: {len(items)} places, {len(prospects)} {args.niche} businesses kept. Skipped: "
              + ", ".join(f"{n} {why}" for why, n in counts.most_common()))
        by_region = Counter(p["region"] or "unknown" for p in prospects)
        print("  by region: " + ", ".join(f"{r} {n}" for r, n in sorted(by_region.items())))

    rows = [plan.upsert(p) for p in prospects]

    def save(label):
        # Save as the run goes, so a long run that stops part-way keeps what it found.
        if not args.dry_run and plan.updates:
            n = write(spreadsheet, plan)
            print(f"  saved to sheet ({label}, {n} ranges)")

    save("new showrooms")
    if not args.dry_run and not args.no_maps:
        print(f"Skipped tab: {write_skipped(spreadsheet, skipped)} businesses listed")

    # Stage 4: Companies House, only for rows never looked up before
    if not args.skip_companies_house:
        key = os.environ.get("COMPANIES_HOUSE_API_KEY") or sys.exit("COMPANIES_HOUSE_API_KEY is not set (or use --skip-companies-house).")
        ch = CompaniesHouse(key)
        todo = [r for r in rows if r and not plan.rows[r]["company_number"] and not plan.rows[r]["company_status"]]
        print(f"Companies House: looking up {len(todo)} companies")
        found = 0
        for i, r in enumerate(todo, 1):
            try:
                info = ch.lookup(plan.rows[r]["company"], plan.rows[r]["postcode"])
            except Exception as e:  # one bad lookup shouldn't stop the run
                print(f"  lookup failed for {plan.rows[r]['company']}: {e}")
                continue
            found += "company_number" in info
            plan.fill(r, info)
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}")
            if i % 500 == 0:
                save(f"Companies House {i}/{len(todo)}")
        print(f"Companies House: matched {found} of {len(todo)}")
        save("Companies House")

    # Stages 2 and 3 check the showrooms found this run (new or already on
    # the sheet, so their ad status is refreshed) plus any row never checked.
    touched = {r for r in rows if r}

    def targets(field):
        out = []
        for r, v in sorted(plan.rows.items()):
            if not str(v.get("company", "")).strip() or is_chain(v["company"], v.get("website", ""), chains):
                continue
            if r in touched or not str(v.get(field, "")).strip():
                out.append(r)
        return out

    failures = []

    # Stage 3 runs before stage 2 so Meta is only checked where there's a gap.
    # Stage 3: Google search for "kitchen showroom [town]"
    try:
        if not args.skip_google:
            todo = [r for r in targets("search_result") if str(plan.rows[r].get("town", "")).strip()]
            if todo:
                if not token:
                    raise RuntimeError("APIFY_TOKEN is not set (or use --skip-google)")
                check_towns = sorted({plan.rows[r]["town"].strip() for r in todo})
                print(f"Google: searching 'kitchen showroom [town]' for {len(check_towns)} towns ({len(todo)} showrooms)")
                searches = google.run_searches(token, check_towns)
                # Stage 1 already ran the same Places search for its towns; reuse
                # its top 3 as the map pack and only look up the rest.
                packs = google.map_packs_from_items(items, [t for t in check_towns if t in towns])
                missing = [t for t in check_towns if t not in packs]
                if missing:
                    if not places_key:
                        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set (or use --skip-google)")
                    packs.update(google.run_map_packs(places_key, missing))
                found = Counter()
                for r in todo:
                    v = plan.rows[r]
                    town = v["town"].strip()
                    res = google.result_for(v["company"], v.get("website", ""), searches.get(town), packs.get(town))
                    found[res["search_result"]] += 1
                    plan.set(r, res)
                print("Google: " + ", ".join(f"{n} {k}" for k, n in found.most_common()))
                if len(searches) < len(check_towns):
                    print(f"  WARNING: no Google result came back for {len(check_towns) - len(searches)} towns")
    except Exception as e:  # keep going: the other stages' results still get written
        failures.append("Google")
        print(f"Google: FAILED, skipped this run ({e})")

    # Stage 2: Meta Ad Library (runs after the Google check, see above)
    try:
        if not args.skip_meta:
            # Only showrooms with a Google gap: the ones already in the map
            # pack or running Google Ads aren't the prospects you want, and
            # every Ad Library result costs money.
            todo = [r for r in targets("running_meta_ads")
                    if plan.rows[r].get("search_result") in ("Not found", "Organic top 10")]
            if todo:
                if not token:
                    raise RuntimeError("APIFY_TOKEN is not set (or use --skip-meta)")
                print(f"Meta: searching the Ad Library for {len(todo)} showrooms")
                ads = meta.run_search(token, [plan.rows[r]["company"] for r in todo], args.ads_per_search)
                running = 0
                for r in todo:
                    v = plan.rows[r]
                    res = meta.result_for(v["company"], v.get("website", ""), ads)
                    running += res["running_meta_ads"] == "Y"
                    plan.set(r, res)
                print(f"Meta: {running} of {len(todo)} running Meta ads ({len(ads)} ads checked)")
    except Exception as e:  # keep going: the other stages' results still get written
        failures.append("Meta")
        print(f"Meta: FAILED, skipped this run ({e})")

    if plan.out_of_room:
        print(f"WARNING: Prospects tab is full; {plan.out_of_room} prospects not added. "
              f"Extend the formulas past row {sheet.ROWS[0]} and raise last_row in niches.py.")

    if args.dry_run:
        out = OUTPUT / f"prospects-{stamp}.csv"
        fields = list(INPUT_COLUMNS)
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sheet_row", *fields])
            for r in sorted(plan.updated_rows):
                w.writerow([r, *[plan.rows[r].get(fld, "") for fld in fields]])
        with open(OUTPUT / f"skipped-{stamp}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["reason", "company", "website", "phone", "postcode", "town",
                                              "google_category", "maps_url", "searched"])
            w.writeheader()
            w.writerows(skipped)
        print(f"Dry run: {plan.added} new, {plan.filled} updated. Written to {out}")
    else:
        save("final")
        print(f"Sheet: {plan.added} new prospects added, {plan.filled} existing rows updated")
    if failures:
        # Fail the workflow so the red run shows something needs a look,
        # after everything that did work has been written.
        sys.exit(f"Finished with failed stages: {', '.join(failures)}")


if __name__ == "__main__":
    main()
