"""Weekly prospect run: Google Maps -> Companies House -> Prospects tab.

    python -m prospector.run --region "West Midlands" --dry-run
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

from . import google, location, maps, meta
from .chains import FALLBACK_CHAINS, is_chain
from .columns import INPUT_COLUMNS
from .companies_house import CompaniesHouse
from .matching import prospect_key
from .sheet import Plan, open_sheet, read_chains, read_prospects, write

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
    if region:
        towns = {t: r for t, r in towns.items() if r.lower() == region.lower()}
    return towns


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--towns", default=str(HERE / "towns.csv"), help="CSV with town,region columns")
    ap.add_argument("--region", help="only run towns in this region, e.g. 'West Midlands'")
    ap.add_argument("--only", help="comma-separated towns to run, e.g. 'Birmingham,Solihull'")
    ap.add_argument("--max-towns", type=int, help="only the first N towns (for a cheap test)")
    ap.add_argument("--max-per-town", type=int, default=40, help="Google Maps results per town (default 40)")
    ap.add_argument("--maps-json", help="reuse a saved Apify result instead of scraping again")
    ap.add_argument("--skip-companies-house", action="store_true")
    ap.add_argument("--skip-meta", action="store_true", help="skip stage 2 (Meta Ad Library)")
    ap.add_argument("--skip-google", action="store_true", help="skip stage 3 (Google search check)")
    ap.add_argument("--no-maps", action="store_true",
                    help="don't search for new showrooms; only fill in ad and search checks for rows already on the sheet")
    ap.add_argument("--ads-per-search", type=int, default=30, help="Meta ads fetched per showroom (default 30)")
    ap.add_argument("--dry-run", action="store_true", help="write a CSV to output/ instead of the sheet")
    args = ap.parse_args(argv)

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
    chains = read_chains(spreadsheet) if spreadsheet else FALLBACK_CHAINS
    plan = Plan(read_prospects(spreadsheet) if spreadsheet else {})
    if not spreadsheet:
        plan.free = list(range(4, 3004))
    print(f"Sheet: {len(plan.index)} prospects already, {len(plan.free)} free rows, {len(chains)} chains excluded")

    # Stage 1: Google Maps
    token = os.environ.get("APIFY_TOKEN", "")
    if args.no_maps:
        items = []
    elif args.maps_json:
        items = json.loads(Path(args.maps_json).read_text())
    else:
        token or sys.exit("APIFY_TOKEN is not set.")
        items = maps.run_search(token, list(towns), args.max_per_town)
        raw = OUTPUT / f"maps-{stamp}.json"
        raw.write_text(json.dumps(items))
        print(f"Saved raw Maps results to {raw} (reuse with --maps-json)")

    counts = Counter()
    prospects, seen = [], set()
    for item in items:
        p, reason = maps.to_prospect(item, towns)
        if not p:
            counts[reason] += 1
            continue
        if is_chain(p["company"], p["website"], chains):
            counts["chain"] += 1
            continue
        # The same showroom often shows up in several towns' searches.
        key = prospect_key(p["website"], p["company"], p["postcode"])
        if key in seen:
            counts["duplicate"] += 1
            continue
        seen.add(key)
        prospects.append(p)

    # Region from each showroom's own postcode, not from the search.
    places = location.lookup(p["postcode"] for p in prospects)
    kept = []
    for p in prospects:
        info = places.get(location.normalise(p["postcode"]))
        region = (info or {}).get("region") or location.region_from_area(p["postcode"]) or p["_searched_region"]
        if location.country_of_region(region) not in COUNTRIES:
            counts["outside England"] += 1
            continue
        p["region"] = region
        kept.append(p)
    # New rows go onto the sheet in postcode order.
    prospects = sorted(kept, key=lambda p: location.sort_key(p["postcode"]))

    if not args.no_maps:
        print(f"Maps: {len(items)} places, {len(prospects)} independent kitchen showrooms kept. Skipped: "
              + ", ".join(f"{n} {why}" for why, n in counts.most_common()))
        by_region = Counter(p["region"] or "unknown" for p in prospects)
        print("  by region: " + ", ".join(f"{r} {n}" for r, n in sorted(by_region.items())))

    rows = [plan.upsert(p) for p in prospects]

    # Stage 4: Companies House, only for rows never looked up before
    if not args.skip_companies_house:
        key = os.environ.get("COMPANIES_HOUSE_API_KEY") or sys.exit("COMPANIES_HOUSE_API_KEY is not set (or use --skip-companies-house).")
        ch = CompaniesHouse(key)
        todo = [r for r in rows if r and not plan.rows[r]["company_number"] and not plan.rows[r]["company_status"]]
        print(f"Companies House: looking up {len(todo)} companies")
        found = 0
        for i, r in enumerate(todo, 1):
            info = ch.lookup(plan.rows[r]["company"], plan.rows[r]["postcode"])
            found += "company_number" in info
            plan.fill(r, info)
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}")
        print(f"Companies House: matched {found} of {len(todo)}")

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

    # Stage 2: Meta Ad Library
    if not args.skip_meta:
        todo = targets("running_meta_ads")
        if todo:
            token or sys.exit("APIFY_TOKEN is not set (or use --skip-meta).")
            print(f"Meta: searching the Ad Library for {len(todo)} showrooms")
            ads = meta.run_search(token, [plan.rows[r]["company"] for r in todo], args.ads_per_search)
            running = 0
            for r in todo:
                v = plan.rows[r]
                res = meta.result_for(v["company"], v.get("website", ""), ads)
                running += res["running_meta_ads"] == "Y"
                plan.set(r, res)
            print(f"Meta: {running} of {len(todo)} running Meta ads ({len(ads)} ads checked)")

    # Stage 3: Google search for "kitchen showroom [town]"
    if not args.skip_google:
        todo = [r for r in targets("search_result") if str(plan.rows[r].get("town", "")).strip()]
        if todo:
            token or sys.exit("APIFY_TOKEN is not set (or use --skip-google).")
            check_towns = sorted({plan.rows[r]["town"].strip() for r in todo})
            print(f"Google: searching 'kitchen showroom [town]' for {len(check_towns)} towns ({len(todo)} showrooms)")
            searches = google.run_searches(token, check_towns)
            # Stage 1 already ran the same search on Maps for its towns; reuse
            # its top 3 as the map pack and only look up the rest.
            packs = google.map_packs_from_items(items, [t for t in check_towns if t in towns])
            missing = [t for t in check_towns if t not in packs]
            packs.update(google.run_map_packs(token, missing))
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

    if plan.out_of_room:
        print(f"WARNING: Prospects tab is full; {plan.out_of_room} prospects not added. Extend formulas past row 3003.")

    if args.dry_run:
        out = OUTPUT / f"prospects-{stamp}.csv"
        fields = list(INPUT_COLUMNS)
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sheet_row", *fields])
            for r in sorted(plan.updates):
                w.writerow([r, *[plan.rows[r].get(fld, "") for fld in fields]])
        print(f"Dry run: {plan.added} new, {plan.filled} updated. Written to {out}")
    else:
        n = write(spreadsheet, plan)
        print(f"Sheet: {plan.added} new prospects added, {plan.filled} existing rows updated ({n} ranges written)")


if __name__ == "__main__":
    main()
