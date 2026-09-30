# Prospector

Finds independent kitchen showrooms and fills the **Prospects** tab of the Cold Lead Template.
The sheet's own formulas then do the scoring, tiering and Call Queue.

This pilot covers two stages:

| Stage | Source | Fills in |
|---|---|---|
| 1. Find showrooms | Apify Google Maps scraper, one "kitchen showroom [town]" search per town | Company, Website, Phone, Town, Postcode, Region, Source, Physical showroom, Google rating, Google reviews |
| 4. Company details | Companies House API (free) | Company number, Company status, Incorporated, Decision maker name, Decision maker role |

Meta ads, Google gap, premium check and TPS are still manual (later stages).

## What it will and won't touch

- It only writes the light-header input columns. Formula columns (I, O, S, AA, AF to AN) are never written.
- It never overwrites a cell that already has something in it. If you correct a phone number, it stays corrected.
- A showroom already on the tab is matched by website domain (or name + postcode area if it has no website), so it's never added twice.
- New showrooms go into the first row with a blank Company Name. Formulas run to row 3003; the run warns if the tab is full.
- National chains on the **Chain Exclusions** tab are skipped. Add a name there and future runs skip it too.
- Companies House is looked up once per showroom. A showroom marked "Not found" isn't retried; clear the cell to retry it.
- Town is the town that was searched, not the Maps address, so the call opener quotes the search you actually ran.

## One-time setup (about 20 minutes)

1. **Apify**: sign up at apify.com, then Settings → API & Integrations → copy the API token.
2. **Companies House**: register at developer.company-information.service.gov.uk, create an application, add a **REST** API key.
3. **Google service account** (lets the script write to your sheet):
   1. console.cloud.google.com → create a project → enable the **Google Sheets API**.
   2. IAM & Admin → Service Accounts → Create. No roles needed.
   3. Open it → Keys → Add key → JSON. A file downloads.
   4. Share the Cold Lead Template with the service account's email (ends `iam.gserviceaccount.com`) as **Editor**.
4. **GitHub secrets**: repo → Settings → Secrets and variables → Actions → New repository secret:
   - `APIFY_TOKEN`
   - `COMPANIES_HOUSE_API_KEY`
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: paste the whole JSON file contents.

   `SHEET_ID` is optional (defaults to the Cold Lead Template). Set it as a repository *variable* to point at a copy.

## Running it

**From GitHub**: Actions → Weekly prospect run → Run workflow. For a first test, use region `West Midlands`, max towns `2`, and tick dry run. Then run it for real.

It runs automatically every Monday at 05:17 UTC, currently for the West Midlands only. Once you've checked the pilot rows, go national by changing `'West Midlands'` to `''` in `.github/workflows/prospector.yml` (and add more towns to `towns.csv`).

**On your own computer**:

```bash
cd prospector
pip install -r requirements.txt
export APIFY_TOKEN=... COMPANIES_HOUSE_API_KEY=...
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

python -m prospector.run --region "West Midlands" --max-towns 2 --dry-run   # CSV in output/, sheet untouched
python -m prospector.run --region "West Midlands"                           # writes to the sheet
```

Each scrape is saved to `output/maps-*.json`. Rerun from it without paying for another scrape with `--maps-json output/maps-....json`.

## Towns

`towns.csv` has `town,region`. Region must match the sheet's Region dropdown exactly (e.g. `Yorkshire & Humber`). The pilot list is 27 West Midlands towns.

## Cost

Apify charges per place scraped. `--max-per-town` (default 40) caps it: 27 towns × 40 = up to 1,080 places per run. Check Apify's current price for the Google Maps scraper and set the schedule and town list to fit your budget. Companies House is free.

## Tests

```bash
pip install pytest && python -m pytest tests
```
