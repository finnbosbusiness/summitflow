"""Weekly prospect run: Google Maps -> Companies House -> Prospects tab.

    python -m prospector.run --region "West Midlands" --dry-run
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import maps
from .chains import FALLBACK_CHAINS, is_chain
from .columns import INPUT_COLUMNS
from .companies_house import CompaniesHouse
from .matching import prospect_key
from .sheet import Plan, open_sheet, read_chains, read_prospects, write

DEFAULT_SHEET_ID = "1OB_KfC0N6aigLFe5-_y8D4_VFD8RW7zRnXEXCKN3ZYQ"
HERE = Path(__file__).resolve().parent.parent
OUTPUT = HERE / "output"


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
    ap.add_argument("--max-towns", type=int, help="only the first N towns (for a cheap test)")
    ap.add_argument("--max-per-town", type=int, default=40, help="Google Maps results per town (default 40)")
    ap.add_argument("--maps-json", help="reuse a saved Apify result instead of scraping again")
    ap.add_argument("--skip-companies-house", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="write a CSV to output/ instead of the sheet")
    args = ap.parse_args(argv)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    OUTPUT.mkdir(exist_ok=True)
    sheet_id = os.environ.get("SHEET_ID", DEFAULT_SHEET_ID)
    has_sheet = bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    if not args.dry_run and not has_sheet:
        sys.exit("No Google credentials set. Set GOOGLE_SERVICE_ACCOUNT_JSON, or use --dry-run.")

    towns = load_towns(args.towns, args.region)
    if args.max_towns:
        towns = dict(list(towns.items())[: args.max_towns])
    if not towns:
        sys.exit("No towns selected. Check --region matches the towns.csv spelling.")
    print(f"{len(towns)} towns: {', '.join(towns)}")

    # Existing sheet state
    spreadsheet = open_sheet(sheet_id) if has_sheet else None
    chains = read_chains(spreadsheet) if spreadsheet else FALLBACK_CHAINS
    plan = Plan(read_prospects(spreadsheet) if spreadsheet else {})
    if not spreadsheet:
        plan.free = list(range(4, 3004))
    print(f"Sheet: {len(plan.index)} prospects already, {len(plan.free)} free rows, {len(chains)} chains excluded")

    # Stage 1: Google Maps
    if args.maps_json:
        items = json.loads(Path(args.maps_json).read_text())
    else:
        token = os.environ.get("APIFY_TOKEN") or sys.exit("APIFY_TOKEN is not set.")
        items = maps.run_search(token, list(towns), args.max_per_town)
        raw = OUTPUT / f"maps-{stamp}.json"
        raw.write_text(json.dumps(items))
        print(f"Saved raw Maps results to {raw} (reuse with --maps-json)")

    prospects, skipped_chain, skipped_other, seen = [], 0, 0, set()
    for item in items:
        p = maps.to_prospect(item, towns)
        if not p:
            skipped_other += 1
            continue
        if is_chain(p["company"], p["website"], chains):
            skipped_chain += 1
            continue
        # The same showroom often shows up in neighbouring towns' searches;
        # keep the first (the town it was found for first).
        key = prospect_key(p["website"], p["company"], p["postcode"])
        if key in seen:
            continue
        seen.add(key)
        prospects.append(p)
    print(f"Maps: {len(items)} places, {len(prospects)} independent kitchen businesses, "
          f"{skipped_chain} chains skipped, {skipped_other} not kitchen/closed")

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
        print(f"Sheet: {plan.added} new prospects added, {plan.filled} existing rows filled in ({n} ranges written)")


if __name__ == "__main__":
    main()
