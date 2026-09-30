# Prospector

Finds independent kitchen showrooms and fills the **Prospects** tab of the Cold Lead Template.
The sheet's own formulas then do the scoring, tiering and Call Queue.

| Stage | Source | Fills in |
|---|---|---|
| 1. Find showrooms | Google Places API, one "kitchen showroom [town]" search per town (20 results) | Company, Website, Phone, Town, Postcode, Region, Source, Physical showroom, Google rating, Google reviews |
| 3. Google check (runs first) | Apify Google Search scraper, one "kitchen showroom [town]" search per town; map pack is the top 3 from the Places search | Running Google Ads, Result for kitchen showroom [town], Competitor ranking instead |
| 2. Meta ads (only showrooms with a Google gap) | Apify Facebook Ad Library scraper, exact-name search, 5 ads max per showroom | Running Meta ads, Oldest live ad start, Meta ad hook |
| 4. Company details | Companies House API (free) | Company number, Company status, Incorporated, Decision maker name, Decision maker role |

Premium check (positioning, brands, budget signals) and TPS are still manual.

### How the Meta and Google checks decide

- **Meta** is only checked for showrooms whose Google result is Not found or Organic top 10. Ones already in the map pack or running Google Ads aren't the prospects you want, and every Ad Library result costs money. The search still returns other advertisers' ads that mention the name, so an active ad only counts if it links to the showroom's website or comes from a Facebook page with the showroom's name. Oldest live ad start is the earliest start date among its active ads (up to 5 checked), and the hook is the first sentence of that ad.
- **Google:** Result is where the showroom shows for "kitchen showroom [its town]": Google Ads if it has an ad there, else Map pack if it's in the top 3 on Google Maps, else Organic top 10, else Not found. Competitor ranking instead is the top map pack showroom that isn't them (or the top organic result that isn't a directory like Houzz or Checkatrade).
- **Running Google Ads** is Y only when they have an ad on that search. They could be advertising on other searches; this measures the search their customers would type.
- These six columns are refreshed whenever a run finds the showroom again, so they can change. Your edits to them will be overwritten; every other column is only ever filled when blank.

## Showroom-only cleanse

Every new find must pass the cleanse in `prospector/cleanse.py` before it's added. Anything that fails goes on the **Skipped** tab with the reason:

| Reason | What it catches |
|---|---|
| Not in the UK | US listings (zip-code postcode or non-UK phone) |
| In liquidation | Companies House says so |
| Not a kitchen business | Cafés, bars, cookware and gift shops, commercial and outdoor kitchens |
| Worktop or stone supplier | Worktop, quartz and granite firms (unless the name also says "kitchens") |
| Trade supplier | Wholesalers, component and door suppliers |
| Fitter or builder, no showroom | Fitters, installers, builders, refurb and respray firms, unless the name says showroom or studio |
| Appliance shop / Furniture or homeware shop | Appliance, range cooker, furniture, bed and antique shops |
| Joinery or furniture maker, not kitchens | Joiners, carpenters and furniture makers without "kitchen" in the name or website |
| No showroom on Google Maps | Google doesn't list it as a store or showroom |
| Kitchens not in name or website | Interiors and design firms that don't say kitchens anywhere |
| No phone number | Nothing to call |

A showroom with the same phone number as one already on the tab, or the same name in the same postcode area, is treated as the same business.

The **All Prospects** tab is the full scrape from before the first cleanse (30 Sep 2026), with a Cleanse result column. Runs never re-add a showroom listed there as removed. To keep one, copy its row back to Prospects; a showroom already on Prospects is never cleansed out.

Companies House matches now need the distinctive part of the name to agree ("ASE Kitchens & Bathrooms" no longer matches "KITCHENS & BATHROOMS LTD"). The cleanse cleared company details that failed that test, so the next run that finds those showrooms looks them up again.

## What it will and won't touch

- It only writes the light-header input columns. Formula columns (I, O, S, AA, AF to AN) are never written.
- It never overwrites a cell that already has something in it. If you correct a phone number, it stays corrected.
- A showroom already on the tab is matched by website domain (or name + postcode area if it has no website), so it's never added twice.
- New showrooms go into the first row with a blank Company Name. Formulas run to row 10003; the run warns if the tab is full.
- A showroom is recognised by its Google place ID as well as its website, so a run never adds the same one twice.
- Everything Google returned that a run left out (chains, branches, other trades, outside England) is listed on the **Skipped** tab with the reason, so nothing disappears without a trace.
- The run saves to the sheet as it goes (after the search, then every 500 Companies House lookups), so a run that stops part-way keeps what it found.
- National chains on the **Chain Exclusions** tab are skipped. Add a name there and future runs skip it too.
- Companies House is looked up once per showroom. A showroom marked "Not found" isn't retried; clear the cell to retry it.
- Location comes from each showroom's own postcode (looked up on postcodes.io, free), not from the town that was searched. A Birmingham search that turns up a Nottingham showroom files it under Nottingham and East Midlands. Showrooms outside England are skipped.
- New rows are added in postcode order (B1, B2 … B10, then CV1 …). Use a filter on Region or Postcode to look at one area.
- Branch pages of bigger businesses (a website like `/showrooms/solihull`) are skipped as chains, and so are businesses that aren't showrooms (appliance stores, worktop suppliers, heating firms, cafés).
- Companies House only matches active companies. A dissolved company with the same name is an old business, not the open showroom, so the row shows "Not found" instead.

## One-time setup (about 20 minutes)

1. **Google Places API**: in the same Google Cloud project as the service account, enable **Places API (New)**, add a billing account, and create an API key (APIs & Services → Credentials). Restrict the key to the Places API.
1. **Apify**: sign up at apify.com, then Settings → API & Integrations → copy the API token.
2. **Companies House**: register at developer.company-information.service.gov.uk, create an application, add a **REST** API key.
3. **Google service account** (lets the script write to your sheet):
   1. console.cloud.google.com → create a project → enable the **Google Sheets API**.
   2. IAM & Admin → Service Accounts → Create. No roles needed.
   3. Open it → Keys → Add key → JSON. A file downloads.
   4. Share the Cold Lead Template with the service account's email (ends `iam.gserviceaccount.com`) as **Editor**.
4. **GitHub secrets**: repo → Settings → Secrets and variables → Actions → New repository secret:
   - `GOOGLE_PLACES_API_KEY`
   - `APIFY_TOKEN`
   - `COMPANIES_HOUSE_API_KEY`
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: paste the whole JSON file contents.

   `SHEET_ID` is optional (defaults to the Cold Lead Template). Set it as a repository *variable* to point at a copy.

## Running it

**From GitHub**: Actions → Prospect run → Run workflow. Tick "only run the Meta and Google checks" to refresh the ad columns on rows already in the sheet without searching for new showrooms. For a first test, use region `West Midlands`, max towns `2`, and tick dry run. Then run it for real.

There is no schedule: run it when your Call Queue is getting low, one region at a time, so you only pay for leads you'll call. To run specific towns, fill in the "Only these towns" box, e.g. `Birmingham,Solihull`.

**On your own computer**:

```bash
cd prospector
pip install -r requirements.txt
export APIFY_TOKEN=... COMPANIES_HOUSE_API_KEY=...
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

export GOOGLE_PLACES_API_KEY=...
python -m prospector.run --region "West Midlands" --max-towns 2 --dry-run   # CSV in output/, sheet untouched
python -m prospector.run --region "West Midlands"                           # writes to the sheet
```

Each search is saved to `output/maps-*.json`. Rerun from it without searching again with `--maps-json output/maps-....json`.

## Towns

`towns.csv` has `town,region`: 538 towns and London districts across England's 9 regions. The region column only decides which run a town belongs to; the region written to the sheet always comes from the showroom's postcode. Add a town by adding a line.

## Cost

- **Places (finding showrooms, map packs):** Google gives a monthly free allowance for Text Search. One search per town is 538 searches for all of England, which should fit inside it. Check the current allowance on Google's Maps Platform pricing page.
- **Google check (Apify Google Search scraper):** one search per town, a few dollars for all of England.
- **Meta check (Apify Ad Library scraper, about $5.80 per 1,000 ads):** only for showrooms with a Google gap, at most 5 ads each. Expect a few dollars per region.
- Companies House and postcodes.io are free.

Every Apify failure (for example the monthly usage limit) is shown in the run log; the other stages still write their results.

## Tests

```bash
pip install pytest && python -m pytest tests
```
