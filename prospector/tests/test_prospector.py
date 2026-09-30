import csv
import json

from prospector import google, location, maps, meta
from prospector.chains import FALLBACK_CHAINS, is_chain
from prospector.columns import FORMULA_COLUMNS, INPUT_COLUMNS, contiguous_blocks
from prospector.companies_house import best_match, format_officer_name, pick_director
from prospector.matching import domain, prospect_key
from prospector.run import DEFAULT_SHEET_ID, main, sheet_id_from
from prospector.sheet import Plan

TOWNS = {"Warwick": "West Midlands", "Leamington Spa": "West Midlands"}


def place(**kw):
    base = {
        "title": "Acme Kitchens", "website": "https://www.acmekitchens.co.uk/", "phone": "01926 000000",
        "city": "Warwick", "postalCode": "CV34 4AB", "totalScore": 4.8, "reviewsCount": 52,
        "categoryName": "Kitchen furniture store", "categories": ["Kitchen furniture store"],
        "searchString": "kitchen showroom Warwick",
    }
    base.update(kw)
    return base


def test_domain():
    assert domain("https://www.Acme.co.uk/contact") == "acme.co.uk"
    assert domain("acme.co.uk") == "acme.co.uk"
    assert domain("https://facebook.com/acme") == ""
    assert domain("") == ""


def test_blocks_never_cover_formula_columns():
    from prospector.columns import col_index, col_letter
    for first, last, _ in contiguous_blocks():
        cols = {col_letter(i) for i in range(col_index(first), col_index(last) + 1)}
        assert not cols & FORMULA_COLUMNS
    assert sum(len(b[2]) for b in contiguous_blocks()) == len(INPUT_COLUMNS)


def test_to_prospect_uses_own_town():
    p, _ = maps.to_prospect(place(city="Royal Leamington Spa", searchString="kitchen showroom Warwick"), TOWNS)
    assert p["town"] == "Royal Leamington Spa"
    assert p["_searched_region"] == "West Midlands"
    assert p["physical_showroom"] == "Y"
    assert p["source"] == "Google Maps"
    assert maps.to_prospect(place(city=""), TOWNS)[0]["town"] == "Warwick"


def test_to_prospect_filters():
    assert maps.to_prospect(place(title="Bob's Plumbing", categoryName="Plumber", categories=[]), TOWNS)[1] == "not kitchen"
    assert maps.to_prospect(place(permanentlyClosed=True), TOWNS)[1] == "closed"
    remodeler, _ = maps.to_prospect(place(categoryName="Kitchen remodeler", categories=[]), TOWNS)
    assert remodeler["physical_showroom"] == ""
    assert maps.to_prospect(place(website="https://facebook.com/acme"), TOWNS)[0]["website"] == ""


def test_off_topic_businesses_skipped():
    def reason(**kw):
        return maps.to_prospect(place(**kw), TOWNS)[1]
    assert reason(title="Brondi | Coffee & Kitchen", categoryName="Coffee machine supplier") == "not kitchen"
    assert reason(title="Macphersons Appliances", categoryName="Appliance store") == "not kitchen"
    assert reason(title="T & S Heating", categoryName="Bathroom supply store", categories=["Kitchen supply store"]) == "not kitchen"
    assert reason(title="Q Stone Quartz & Kitchens", categoryName="Countertop store") == "not kitchen"
    # A kitchen showroom that also sells appliances stays in
    assert reason(title="Connelly's Kitchens & Appliances", categoryName="Kitchen furniture store") is None


def test_branch_pages_and_clean_urls():
    assert maps.to_prospect(place(website="https://home-design-schmidt.uk/showrooms/solihull/"), TOWNS)[1] == "branch"
    assert maps.to_prospect(place(website="https://mkm.com/branches/nottingham?utm_source=GMB"), TOWNS)[1] == "branch"
    p, _ = maps.to_prospect(place(website="https://www.avantikb.co.uk/?utm_source=google&utm_medium=gmb"), TOWNS)
    assert p["website"] == "https://www.avantikb.co.uk/"


def test_location():
    assert location.normalise("b913jw") == "B91 3JW"
    assert location.normalise("B91") == ""
    assert location.region_from_area("NG17 7LF") == "East Midlands"
    assert location.region_from_area("S18 2GG") == "Yorkshire & Humber"
    assert location.country_of_region("Wales") == "Wales"
    assert location.country_of_region("London") == "England"
    pcs = ["CV34 4AB", "B10 1AA", "", "B2 4QA", "B91 3JW"]
    assert sorted(pcs, key=location.sort_key) == ["B2 4QA", "B10 1AA", "B91 3JW", "CV34 4AB", ""]


def test_postcodes_io_parsing():
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": 200, "result": [
                {"query": "NG17 7LF", "result": {"region": "East Midlands", "country": "England"}},
                {"query": "HU1 1AA", "result": {"region": "Yorkshire and The Humber", "country": "England"}},
                {"query": "CF10 1AA", "result": {"region": None, "country": "Wales"}},
                {"query": "ZZ1 1ZZ", "result": None},
            ]}

    class Session:
        def post(self, url, json, timeout):
            return Resp()

    out = location.lookup(["NG17 7LF", "hu11aa", "CF10 1AA", "ZZ1 1ZZ", ""], session=Session())
    assert out["NG17 7LF"]["region"] == "East Midlands"
    assert out["HU1 1AA"]["region"] == "Yorkshire & Humber"
    assert out["CF10 1AA"]["region"] == "Wales"
    assert "ZZ1 1ZZ" not in out


def test_chain():
    assert is_chain("Howdens Joinery", "howdens.com", FALLBACK_CHAINS)
    assert not is_chain("Acme Kitchens", "acmekitchens.co.uk", FALLBACK_CHAINS)


def test_plan_adds_to_first_blank_row_and_never_overwrites():
    rows = {n: {f: "" for f in INPUT_COLUMNS} for n in range(4, 10)}
    rows[4].update(company="Acme Kitchens", website="acmekitchens.co.uk", phone="MY EDIT", google_rating="")
    plan = Plan(rows)

    # Same showroom (matched on domain, despite a different name) -> row 4, only blanks filled
    assert plan.upsert({"company": "Acme Kitchens Ltd", "website": "https://www.acmekitchens.co.uk", "phone": "999", "google_rating": 4.8}) == 4
    assert plan.updates[4] == {"google_rating": 4.8}
    assert plan.rows[4]["phone"] == "MY EDIT"

    # New showroom -> first free row
    assert plan.upsert({"company": "Beta Kitchens", "website": "", "postcode": "CV34 1AA"}) == 5
    assert plan.added == 1 and plan.filled == 1

    ranges = plan.value_ranges()
    assert {"range": "Prospects!J4:N4", "values": [[None, 4.8, None, None, None]]} in ranges
    assert all(":" in r["range"] for r in ranges)


def test_text_fields_are_protected():
    plan = Plan({4: {f: "" for f in INPUT_COLUMNS}})
    plan.upsert({"company": "Acme", "phone": "01926 000000", "company_number": "01234567"})
    flat = [v for r in plan.value_ranges() for v in r["values"][0]]
    assert "'01926 000000" in flat and "'01234567" in flat and "Acme" in flat
    assert maps.uk_phone("+44 1926 000000") == "01926 000000"


def test_plan_full_sheet():
    plan = Plan({4: {f: "" for f in INPUT_COLUMNS} | {"company": "X"}})
    assert plan.upsert({"company": "Y", "website": "y.com"}) is None
    assert plan.out_of_room == 1


def test_best_match():
    cands = [
        {"title": "ACME KITCHENS (MIDLANDS) LIMITED", "company_number": "1", "company_status": "dissolved",
         "address_snippet": "1 High St, Leeds, LS1 1AA", "company_type": "ltd"},
        {"title": "ACME KITCHEN STUDIO LTD", "company_number": "2", "company_status": "active",
         "address_snippet": "5 Smith St, Warwick, CV34 4AB", "company_type": "ltd"},
        {"title": "TOTALLY DIFFERENT LTD", "company_number": "3", "company_status": "active",
         "address_snippet": "Warwick CV34 4AB", "company_type": "ltd"},
    ]
    assert best_match("Acme Kitchen Studio", "CV34 4AB", cands)["company_number"] == "2"
    assert best_match("Nothing Alike", "B1 1AA", cands) is None


def test_best_match_skips_dissolved_and_non_english():
    dissolved = [{"title": "REEHAL KITCHENS LIMITED", "company_number": "07004509", "company_status": "dissolved",
                  "address_snippet": "Birmingham B21 0LH", "company_type": "ltd"}]
    assert best_match("Reehal Kitchens", "B21 0LH", dissolved) is None
    ni = [{"title": "CHOICE INTERIORS LTD", "company_number": "NI628519", "company_status": "active",
           "address_snippet": "Belfast BT1 1AA", "company_type": "ltd"}]
    assert best_match("Choice Interiors", "B11 2EX", ni) is None


def test_director():
    officers = [
        {"name": "JONES, Mary", "officer_role": "secretary", "appointed_on": "2001-01-01"},
        {"name": "SMITH, Mr John Paul", "officer_role": "director", "appointed_on": "2010-05-01"},
        {"name": "BROWN, Alan", "officer_role": "director", "appointed_on": "2005-01-01", "resigned_on": "2012-01-01"},
        {"name": "GREEN, Sue", "officer_role": "director", "appointed_on": "2018-01-01"},
    ]
    assert pick_director(officers) == "John Paul Smith"
    assert format_officer_name("ACME HOLDINGS LIMITED") == "ACME HOLDINGS LIMITED"
    assert format_officer_name("BRUNDRETT, Richard,") == "Richard Brundrett"
    assert format_officer_name("BOGUE, Seamus Anthony,") == "Seamus Anthony Bogue"
    assert format_officer_name("BOGUE, Seamus Anthony, Mr") == "Seamus Anthony Bogue"


def test_dry_run_end_to_end(tmp_path, monkeypatch, capsys):
    items = [
        place(),
        place(searchString="kitchen showroom Leamington Spa"),  # duplicate in second town
        place(title="Howdens", website="https://howdens.com"),
        place(title="Beta Kitchen Design", website="", postalCode="B91 5AA", searchString="kitchen showroom Leamington Spa"),
        # Found by a West Midlands search but actually in Nottingham: kept, labelled East Midlands
        place(title="Charles Yorke", website="https://www.charlesyorke.com/", postalCode="NG17 7LA", city="Kirkby-in-Ashfield"),
        # In Wales: dropped
        place(title="Cardiff Kitchens", website="https://cardiffkitchens.co.uk", postalCode="CF10 1AA", city="Cardiff"),
    ]
    monkeypatch.setattr("prospector.location.lookup", lambda pcs, session=None: {})
    f = tmp_path / "items.json"
    f.write_text(json.dumps(items))
    towns = tmp_path / "towns.csv"
    towns.write_text("town,region\nWarwick,West Midlands\nLeamington Spa,West Midlands\n")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("prospector.run.OUTPUT", tmp_path)
    main(["--towns", str(towns), "--maps-json", str(f), "--skip-companies-house", "--skip-meta", "--skip-google", "--dry-run"])
    out = capsys.readouterr().out
    assert "3 independent kitchen showrooms kept" in out
    assert "1 chain" in out and "1 duplicate" in out and "1 outside England" in out
    rows = list(csv.DictReader(next(tmp_path.glob("prospects-*.csv")).open()))
    # Sorted by postcode: B91, CV34, NG17
    assert [r["company"] for r in rows] == ["Beta Kitchen Design", "Acme Kitchens", "Charles Yorke"]
    assert [r["sheet_row"] for r in rows] == ["4", "5", "6"]
    assert rows[2]["region"] == "East Midlands" and rows[2]["town"] == "Kirkby-in-Ashfield"


def test_sheet_id():
    assert sheet_id_from("") == DEFAULT_SHEET_ID
    assert sheet_id_from(None) == DEFAULT_SHEET_ID
    assert sheet_id_from("https://docs.google.com/spreadsheets/d/abc_123-X/edit#gid=0") == "abc_123-X"
    assert sheet_id_from("abc") == "abc"


# ---- Stage 2: Meta ----

def ad(page="Madina Kitchens", link="https://www.madinakitchens.co.uk/offer", start=1788073200, text="Spring sale: 20% off all kitchens. Book a visit!", active=True):
    # Shape taken from a real apify/facebook-ads-scraper item
    return {"pageName": page, "isActive": active, "startDate": start,
            "startDateFormatted": "2026-08-30T07:00:00.000Z",
            "snapshot": {"pageName": page, "linkUrl": link, "caption": "", "body": {"text": text}, "cards": []}}


def test_meta_search_name():
    assert meta.search_name("Avanti | Kitchens, Bathrooms and Bedrooms Showroom | Solihull") == "Avanti"
    assert meta.search_name("Madina Kitchens Ltd") == "Madina Kitchens"
    assert "q=Madina+Kitchens" in meta.library_url("Madina Kitchens Ltd")


def test_meta_matches_on_domain_or_page_name_only():
    other = ad(page="Social Presence.uk", link="https://socialpresence.info/")
    r = meta.result_for("Madina Kitchens Ltd", "https://www.madinakitchens.co.uk/", [other])
    assert r == {"running_meta_ads": "N", "oldest_ad_start": "", "meta_ad_hook": ""}

    by_domain = ad(page="MK Interiors Birmingham")
    assert meta.result_for("Madina Kitchens Ltd", "https://www.madinakitchens.co.uk/", [by_domain])["running_meta_ads"] == "Y"

    by_page = ad(link="https://fb.me/xyz")
    assert meta.result_for("Madina Kitchens Ltd", "", [by_page])["running_meta_ads"] == "Y"

    inactive = ad(active=False)
    assert meta.result_for("Madina Kitchens Ltd", "https://www.madinakitchens.co.uk/", [inactive])["running_meta_ads"] == "N"


def test_meta_oldest_ad_and_hook():
    newer = ad(start=1790000000, text="New range just landed.")
    older = ad(start=1780000000, text="Spring sale: 20% off all kitchens. Book a visit!")
    r = meta.result_for("Madina Kitchens", "https://www.madinakitchens.co.uk/", [newer, older])
    assert r["oldest_ad_start"] == "2026-05-28"
    assert r["meta_ad_hook"] == "Spring sale: 20% off all kitchens."
    assert meta.hook(ad(text="{{product.name}} from {{product.price}}")) == ""


# ---- Stage 3: Google ----

def serp(organic=(), paid=()):
    # Shape taken from a real apify/google-search-scraper item
    org = [{"title": "Map", "url": "https://www.google.co.uk/search?q=kitchen+showroom+Solihull", "position": 1}]
    org += [{"title": t, "url": u, "position": i + 2} for i, (t, u) in enumerate(organic)]
    return {"searchQuery": {"term": "kitchen showroom Solihull"}, "organicResults": org,
            "paidResults": [{"title": t, "url": u} for t, u in paid]}


PACK = [{"title": "Cucina Kitchens", "website": "http://www.cucina-kitchens.co.uk/", "rank": 1},
        {"title": "Kitchen Gallery SieMatic", "website": "https://kitchengallery.co.uk/", "rank": 2},
        {"title": "Reflections Studio", "website": "http://rstudio.co.uk/", "rank": 3}]


def test_google_not_found_names_competitor():
    s = serp(organic=[("Houzz - Best Kitchen Designers", "https://www.houzz.co.uk/x"),
                      ("Plum Kitchens | Solihull Showroom", "https://www.plumkitchens.co.uk/")])
    r = google.result_for("Madina Kitchens", "https://www.madinakitchens.co.uk/", s, PACK)
    assert r == {"running_google_ads": "N", "search_result": "Not found", "competitor_ranking": "Cucina Kitchens"}
    r = google.result_for("Madina Kitchens", "https://www.madinakitchens.co.uk/", s, [])
    assert r["competitor_ranking"] == "Plum Kitchens"  # directories skipped


def test_google_positions():
    s = serp(organic=[("Plum Kitchens | Solihull", "https://www.plumkitchens.co.uk/")],
             paid=[("Avanti", "https://www.avantikb.co.uk/offer")])
    assert google.result_for("Avanti", "https://www.avantikb.co.uk/", s, PACK)["search_result"] == "Google Ads"
    assert google.result_for("Avanti", "https://www.avantikb.co.uk/", s, PACK)["running_google_ads"] == "Y"
    assert google.result_for("Kitchen Gallery", "https://kitchengallery.co.uk/", s, PACK)["search_result"] == "Map pack"
    r = google.result_for("Plum Kitchens", "https://www.plumkitchens.co.uk/", s, PACK)
    assert r["search_result"] == "Organic top 10" and r["competitor_ranking"] == "Cucina Kitchens"
    # The google.co.uk "Map" placeholder never counts as the showroom's own listing
    assert google.result_for("Map", "", s, [])["search_result"] == "Not found"


def test_map_packs_from_stage1_items():
    items = [{"searchString": "kitchen showroom Solihull", "title": t, "rank": r} for t, r in
             [("D", 4), ("B", 2), ("A", 1), ("C", 3)]]
    assert [p["title"] for p in google.map_packs_from_items(items, ["Solihull"])["Solihull"]] == ["A", "B", "C"]


def test_plan_set_overwrites_and_clears():
    rows = {4: {f: "" for f in INPUT_COLUMNS} | {"company": "Acme", "running_meta_ads": "Y", "meta_ad_hook": "Old"}}
    plan = Plan(rows)
    plan.set(4, {"running_meta_ads": "N", "meta_ad_hook": "", "oldest_ad_start": ""})
    assert plan.updates[4] == {"running_meta_ads": "N", "meta_ad_hook": ""}
    flat = [v for r in plan.value_ranges() for v in r["values"][0]]
    assert "" in flat and "'" not in flat  # a cleared cell is "", not a lone apostrophe


def test_stages_2_and_3_end_to_end(tmp_path, monkeypatch, capsys):
    items = [place(title="Madina Kitchens", website="https://www.madinakitchens.co.uk/", postalCode="B12 8DN",
                   city="Birmingham", searchString="kitchen showroom Birmingham", rank=5),
             place(title="Cucina Kitchens", website="http://www.cucina-kitchens.co.uk/", postalCode="B94 5JU",
                   city="Birmingham", searchString="kitchen showroom Birmingham", rank=1),
             # Chains are skipped as prospects but still take map pack places
             place(title="Howdens", website="https://howdens.com", searchString="kitchen showroom Birmingham", rank=2),
             place(title="Wren Kitchens", website="https://wrenkitchens.com", searchString="kitchen showroom Birmingham", rank=3)]
    f = tmp_path / "items.json"
    f.write_text(json.dumps(items))
    towns = tmp_path / "towns.csv"
    towns.write_text("town,region\nBirmingham,West Midlands\n")
    calls = []

    def fake_actor(token, actor, payload, timeout_s=0):
        calls.append(actor)
        if actor == meta.ACTOR:
            return [ad()]
        if actor == google.SEARCH_ACTOR:
            return [serp() | {"searchQuery": {"term": "kitchen showroom Birmingham"}}]
        raise AssertionError(f"unexpected actor {actor}")

    monkeypatch.setattr("prospector.meta.run_actor", fake_actor)
    monkeypatch.setattr("prospector.google.run_actor", fake_actor)
    monkeypatch.setattr("prospector.location.lookup", lambda pcs, session=None: {})
    monkeypatch.setenv("APIFY_TOKEN", "x")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("prospector.run.OUTPUT", tmp_path)
    main(["--towns", str(towns), "--maps-json", str(f), "--skip-companies-house", "--dry-run"])
    # Map pack reused from the stage 1 Maps results: no extra Maps run
    assert calls == [meta.ACTOR, google.SEARCH_ACTOR]
    rows = {r["company"]: r for r in csv.DictReader(next(tmp_path.glob("prospects-*.csv")).open())}
    assert rows["Madina Kitchens"]["running_meta_ads"] == "Y"
    assert rows["Madina Kitchens"]["search_result"] == "Not found"
    assert rows["Madina Kitchens"]["competitor_ranking"] == "Cucina Kitchens"
    assert rows["Cucina Kitchens"]["search_result"] == "Map pack"
    assert rows["Cucina Kitchens"]["running_meta_ads"] == "N"
