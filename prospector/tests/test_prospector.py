import csv
import json

from prospector import google, location, maps, meta, places
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
    # Rows 4 and 5 are consecutive, so each column block is one two-row range
    assert {"range": "Prospects!J4:N5", "values": [[None, 4.8, None, None, None], [None, None, None, None, None]]} in ranges
    assert {"range": "Prospects!B4:H5", "values": [[None, None, None, None, None, None, None],
                                                  ["Beta Kitchens", None, None, None, "'CV34 1AA", None, None]]} in ranges
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
        place(title="Howdens", website="https://howdens.com", phone="01926 000001"),
        place(title="Beta Kitchen Design", website="", postalCode="B91 5AA", searchString="kitchen showroom Leamington Spa",
              phone="0121 000 0002"),
        # Found by a West Midlands search but actually in Nottingham: kept, labelled East Midlands
        place(title="Charles Yorke Kitchens", website="https://www.charlesyorke.com/", postalCode="NG17 7LA",
              city="Kirkby-in-Ashfield", phone="01623 000003"),
        # In Wales: dropped
        place(title="Cardiff Kitchens", website="https://cardiffkitchens.co.uk", postalCode="CF10 1AA", city="Cardiff",
              phone="029 0000 0004"),
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
    assert [r["company"] for r in rows] == ["Beta Kitchen Design", "Acme Kitchens", "Charles Yorke Kitchens"]
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
                   city="Birmingham", searchString="kitchen showroom Birmingham", rank=1, phone="0121 000 0005"),
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
    # Map pack reused from the stage 1 results: no extra Places search
    assert calls == [google.SEARCH_ACTOR, meta.ACTOR]
    rows = {r["company"]: r for r in csv.DictReader(next(tmp_path.glob("prospects-*.csv")).open())}
    assert rows["Madina Kitchens"]["running_meta_ads"] == "Y"
    assert rows["Madina Kitchens"]["search_result"] == "Not found"
    assert rows["Madina Kitchens"]["competitor_ranking"] == "Cucina Kitchens"
    assert rows["Cucina Kitchens"]["search_result"] == "Map pack"
    # Already in the map pack: not a gap prospect, so no paid Meta lookup
    assert rows["Cucina Kitchens"]["running_meta_ads"] == ""


def test_failed_stage_does_not_stop_the_run(tmp_path, monkeypatch, capsys):
    items = [place(title="Madina Kitchens", website="https://www.madinakitchens.co.uk/", postalCode="B12 8DN",
                   city="Birmingham", searchString="kitchen showroom Birmingham", rank=1)]
    f = tmp_path / "items.json"
    f.write_text(json.dumps(items))
    towns = tmp_path / "towns.csv"
    towns.write_text("town,region\nBirmingham,West Midlands\n")

    def fake_actor(token, actor, payload, timeout_s=0):
        raise RuntimeError("failed to start (403): Monthly usage hard limit exceeded")

    monkeypatch.setattr("prospector.meta.run_actor", fake_actor)
    monkeypatch.setattr("prospector.google.run_actor", fake_actor)
    monkeypatch.setattr("prospector.location.lookup", lambda pcs, session=None: {})
    monkeypatch.setenv("APIFY_TOKEN", "x")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("prospector.run.OUTPUT", tmp_path)
    try:
        main(["--towns", str(towns), "--maps-json", str(f), "--skip-companies-house", "--dry-run"])
        raise AssertionError("expected a non-zero exit")
    except SystemExit as e:
        assert "Google" in str(e)
    out = capsys.readouterr().out
    assert "Google: FAILED" in out and "usage hard limit" in out
    # The new showroom is still written
    row = next(csv.DictReader(next(tmp_path.glob("prospects-*.csv")).open()))
    assert row["company"] == "Madina Kitchens" and row["search_result"] == ""


# ---- Google Places ----

def gplace(name, pc="B91 3JW", town="Solihull", site="https://example.co.uk/", types=None, primary="General contractor",
           status="OPERATIONAL"):
    # Shape taken from a real Places API (New) Text Search response
    return {"displayName": {"text": name}, "websiteUri": site, "nationalPhoneNumber": "07791 687261",
            "rating": 5, "userRatingCount": 25, "businessStatus": status,
            "primaryTypeDisplayName": {"text": primary},
            "types": types or ["furniture_store", "home_improvement_store", "general_contractor", "home_goods_store", "store"],
            "addressComponents": [
                {"longText": "Widney Manor Road", "types": ["route"]},
                {"longText": town, "types": ["postal_town"]},
                {"longText": "England", "types": ["administrative_area_level_1", "political"]},
                {"longText": pc, "types": ["postal_code"]}]}


def test_places_item_conversion():
    it = places.to_item(gplace("Kitchens of Solihull LTD"), "kitchen showroom Solihull", 1)
    assert (it["title"], it["city"], it["postalCode"], it["rank"]) == ("Kitchens of Solihull LTD", "Solihull", "B91 3JW", 1)
    assert "furniture store" in it["categories"] and it["_trusted"]
    p, _ = maps.to_prospect(it, {"Solihull": "West Midlands"})
    assert p["physical_showroom"] == "Y" and p["google_rating"] == 5 and p["town"] == "Solihull"
    assert places.to_item(gplace("X", status="CLOSED_PERMANENTLY"), "q", 1)["permanentlyClosed"]


def test_places_relevance_trusts_google_but_drops_other_trades():
    def reason(name, **kw):
        return maps.to_prospect(places.to_item(gplace(name, **kw), "kitchen showroom Solihull", 1), {})[1]
    assert reason("No Thirty One") is None
    assert reason("Culina+Balneo") is None
    assert reason("Bluewater KBB") is None
    assert reason("Bathroom Centre") == "not kitchen"
    assert reason("Macphersons Appliances") == "not kitchen"
    assert reason("Howdens - Solihull", primary="Hardware store") == "not kitchen"
    # Real categories Google gave non-showrooms in the West Midlands run
    assert reason("One and Every", primary="Gift shop") == "not kitchen"
    assert reason("Purple Kidderminster", primary="Suppliers") == "not kitchen"
    assert reason("Worcestershire Tile & Stone", primary="Building materials store") == "not kitchen"
    assert reason("1st Call 24/7 Limited", primary="Plumber") == "not kitchen"
    assert reason("Taps UK", primary="Home goods store") == "not kitchen"
    assert reason("Supervalue Furniture & Bed Centre", primary="Furniture store") == "not kitchen"
    assert reason("Fire and Garden Ltd", primary="Home improvement store") == "not kitchen"
    assert reason("Impact Joinery", primary="Furniture store") is None
    assert reason("KAW Interior Design", primary="General contractor") is None
    assert reason("Little Al's Kitchen", primary="Food") == "not kitchen"
    assert reason("Brew & Eat", primary="Coffee shop") == "not kitchen"


def test_places_outside_uk_dropped():
    us = gplace("S&S Kitchens", pc="01453", town="Leominster")
    us["addressComponents"].append({"longText": "United States", "shortText": "US", "types": ["country", "political"]})
    assert maps.to_prospect(places.to_item(us, "kitchen showroom Leominster", 1), {})[1] == "outside UK"
    gb = gplace("Heritage Oak Kitchens")
    gb["addressComponents"].append({"longText": "United Kingdom", "shortText": "GB", "types": ["country", "political"]})
    assert maps.to_prospect(places.to_item(gb, "kitchen showroom Leominster", 1), {})[1] is None


def test_multi_site_domains():
    items = [{"website": "https://www.classicinteriors.co.uk/", "postalCode": pc} for pc in ("B91 1BQ", "CV32 4DW", "WR5 1AA")]
    items += [{"website": "https://www.kitchenfactoryshowroom.co.uk/", "postalCode": "DY2 9NP"},
              {"website": "https://www.kitchenfactoryshowroom.co.uk/", "postalCode": "DY2 9NP"}]
    assert maps.multi_site_domains(items) == {"classicinteriors.co.uk"}


def test_places_pagination():
    calls = []

    class Resp:
        def __init__(self, data):
            self.status_code, self._data, self.text = 200, data, ""

        def json(self):
            return self._data

    class Session:
        def post(self, url, json, headers, timeout):
            calls.append(json.get("pageToken"))
            if not json.get("pageToken"):
                return Resp({"places": [gplace(f"A{i}") for i in range(20)], "nextPageToken": "t2"})
            return Resp({"places": [gplace(f"B{i}") for i in range(20)]})

    assert len(places.search("k", "q", 20, Session())) == 20 and calls == [None]
    calls.clear()
    assert len(places.search("k", "q", 30, Session())) == 30 and calls == [None, "t2"]
    items = places.run_search("k", ["kitchen showroom Solihull"], 3, Session())
    assert [i["rank"] for i in items] == [1, 2, 3]


def test_stage1_searches_places_per_town(tmp_path, monkeypatch, capsys):
    seen = []

    def fake_search(key, queries, max_per_query=20, session=None, keep_paging=None, progress=None):
        seen.append((key, list(queries), max_per_query))
        return [places.to_item(gplace("No Thirty One Kitchens", pc="B93 0HL"), "kitchen showroom Solihull", 1)]

    monkeypatch.setattr("prospector.places.run_search", fake_search)
    monkeypatch.setattr("prospector.location.lookup", lambda pcs, session=None: {})
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("prospector.run.OUTPUT", tmp_path)
    towns = tmp_path / "towns.csv"
    towns.write_text("town,region\nSolihull,West Midlands\n")
    main(["--towns", str(towns), "--skip-companies-house", "--skip-meta", "--skip-google", "--dry-run"])
    assert seen == [("k", ["kitchen showroom Solihull"], 60)]
    row = next(csv.DictReader(next(tmp_path.glob("prospects-*.csv")).open()))
    assert row["company"] == "No Thirty One Kitchens" and row["region"] == "West Midlands"


def test_place_id_dedupe_and_new_fields():
    rows = {n: {f: "" for f in INPUT_COLUMNS} for n in range(4, 8)}
    rows[4].update(company="No Thirty One", place_id="ChIJabc", postcode="B93 0HL")
    plan = Plan(rows)
    # Same place ID, different spelling and no website: still the same showroom
    assert plan.upsert({"company": "No. 31 Kitchens", "place_id": "ChIJabc", "address": "1 High St, Knowle",
                        "maps_url": "https://maps.google.com/?cid=1"}) == 4
    assert plan.updates[4] == {"address": "1 High St, Knowle", "maps_url": "https://maps.google.com/?cid=1"}
    assert plan.added == 0
    ranges = plan.value_ranges()
    assert {"range": "Prospects!AO4:AR4", "values": [["'1 High St, Knowle", "https://maps.google.com/?cid=1", None, None]]} in ranges


def test_places_paging_stops_when_results_stop_being_relevant():
    pages = {None: ([gplace(f"A{i} Kitchens") for i in range(20)], "t2"),
             "t2": ([gplace(f"Bathroom World {i}") for i in range(20)], "t3"),
             "t3": ([gplace(f"C{i} Kitchens") for i in range(20)], None)}
    calls = []

    class Resp:
        status_code, text = 200, ""

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class Session:
        def post(self, url, json, headers, timeout):
            tok = json.get("pageToken")
            calls.append(tok)
            ps, nxt = pages[tok]
            return Resp({"places": ps, **({"nextPageToken": nxt} if nxt else {})})

    def relevant(items):
        return sum(1 for it in items if maps.to_prospect(it, {})[0]) >= 8

    items = places.run_search("k", ["kitchen showroom X"], 60, Session(), keep_paging=relevant)
    # Page 1 was all kitchens so page 2 was fetched; page 2 wasn't, so page 3 wasn't
    assert calls == [None, "t2"] and len(items) == 40
    new = places.to_item(gplace("A"), "q", 1)
    assert set(("address", "mapsUrl", "placeId")) <= set(new)


def test_skipped_businesses_are_recorded(tmp_path, monkeypatch):
    items = [place(title="Madina Kitchens", website="https://www.madinakitchens.co.uk/", postalCode="B12 8DN"),
             place(title="Howdens Joinery", website="https://howdens.com", postalCode="B1 1AA"),
             place(title="Brondi | Coffee & Kitchen", categoryName="Coffee machine supplier", postalCode="B93 8HH")]
    f = tmp_path / "items.json"
    f.write_text(json.dumps(items))
    towns = tmp_path / "towns.csv"
    towns.write_text("town,region\nWarwick,West Midlands\n")
    monkeypatch.setattr("prospector.location.lookup", lambda pcs, session=None: {})
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("prospector.run.OUTPUT", tmp_path)
    main(["--towns", str(towns), "--region", "England", "--maps-json", str(f), "--skip-companies-house",
          "--skip-meta", "--skip-google", "--dry-run"])
    sk = {r["company"]: r["reason"] for r in csv.DictReader(next(tmp_path.glob("skipped-*.csv")).open())}
    assert sk == {"Howdens Joinery": "chain", "Brondi | Coffee & Kitchen": "not kitchen"}


def row(**kw):
    base = {"company": "Acme Kitchens", "website": "https://www.acmekitchens.co.uk/", "phone": "01926 000000",
            "postcode": "CV34 4AB", "physical_showroom": "Y", "google_category": "Furniture store",
            "company_status": "Active", "google_reviews": "52"}
    base.update(kw)
    return base


def test_cleanse_keeps_kitchen_showrooms():
    from prospector.cleanse import reason
    assert reason(row()) == ""
    assert reason(row(company="Leeds Trade Kitchens")) == ""  # trade-price showrooms sell to the public
    assert reason(row(company="GM Kitchens and Worktops")) == ""
    assert reason(row(company="Glotech Kitchen & Appliance Showroom")) == ""
    assert reason(row(company="Charnay", website="http://www.charnaykitchens.co.uk/")) == ""
    assert reason(row(company="Acme Kitchen Studio", physical_showroom="")) == ""


def test_cleanse_removes_what_isnt_a_showroom():
    from prospector.cleanse import reason
    assert reason(row(company_status="Liquidation")) == "In liquidation"
    assert reason(row(company="The Salad Kitchen", google_category="Salad shop")) == "Not a kitchen business"
    assert reason(row(company="Fulcrum Commercial Kitchens Ltd")) == "Not a kitchen business"
    assert reason(row(company="Quartz Kitchen Worktops - QuartzMatik")) == "Worktop or stone supplier"
    assert reason(row(company="Wilson's Trade Kitchens & Components Wholesale")) == "Trade supplier"
    assert reason(row(company="Northampton kitchen fitters")) == "Fitter or builder, no showroom"
    assert reason(row(company="Chester Kitchen Revamps")) == "Fitter or builder, no showroom"
    assert reason(row(company="Whitakers Of Shipley Kitchen Appliances")) == "Appliance shop"
    assert reason(row(company="Cheap Furniture Warehouse", website="")) == "Furniture or homeware shop"
    assert reason(row(company="ACR Woodworking", website="")) == "Joinery or furniture maker, not kitchens"
    assert reason(row(physical_showroom="")) == "No showroom on Google Maps"
    assert reason(row(company="Spires Interiors", website="https://spires.co.uk/")) == "Kitchens not in name or website"
    assert reason(row(phone="")) == "No phone number"
    assert reason(row(phone="(978) 466-9600", postcode="01453")) == "Not in the UK"


def test_cleanse_duplicates_keep_the_fuller_row():
    from prospector.cleanse import find_duplicates
    rows = [
        row(company="Your Beautiful Kitchen", website="", postcode="GU15 2QR", phone="01252 522400"),
        row(company="Your Beautiful Kitchen", website="http://yourbeautifulkitchen.co.uk/", postcode="GU16 6EZ",
            phone="+44 1252 522400"),
        row(company="Victoria Kitchens", postcode="SE7 7AJ", phone="020 0000 0001", company_number="1"),
        row(company="Victoria Kitchens", postcode="SM4 6EP", phone="020 0000 0002", company_number="1"),
        row(company="Kitchen Studio Doncaster", postcode="DN2 4NY", phone="01302 000001", website=""),
        row(company="Kitchen Studio Doncaster", postcode="DN2 5HU", phone="01302 000002", website=""),
    ]
    # Same phone, and same name in the same postcode area; a shared
    # company number alone doesn't make two showrooms one business.
    assert find_duplicates(rows) == {0: 1, 5: 4}


def test_unreliable_company_numbers():
    from prospector.cleanse import unreliable_company_numbers
    rows = [
        row(company="ASE Kitchens & Bathrooms", company_number="09626308"),
        row(company="ATD Kitchens & Bathrooms", company_number="09626308"),
        row(company="Victoria Kitchens", postcode="SE7 7AJ", company_number="2"),
        row(company="Victoria Kitchens", postcode="SM4 6EP", company_number="2"),
        row(company="The Kitchen Centre", company_number="3"),
        row(company="Norton Kitchen & Bedroom", company_number="4"),
    ]
    assert unreliable_company_numbers(rows) == {"09626308", "2", "3"}


def test_best_match_needs_the_distinctive_words():
    generic = [{"title": "KITCHENS & BATHROOMS LTD", "company_number": "09626308", "company_status": "active",
                "address_snippet": "London N1 1AA", "company_type": "ltd"}]
    assert best_match("ASE Kitchens & Bathrooms", "SY8 1XD", generic) is None
    hawk = [{"title": "HAWK KITCHENS & BATHROOMS LTD", "company_number": "07653840", "company_status": "active",
             "address_snippet": "St Albans AL3 8AQ", "company_type": "ltd"}]
    assert best_match("Hawkins Kitchens and Bathrooms Limited", "HP3 9NG", hawk) is None
    assert best_match("Hawk Kitchens & Bathrooms", "AL3 8AQ", hawk)["company_number"] == "07653840"
    # A longer registered name only counts in the same postcode area
    sutton = [{"title": "SUTTON KITCHENS (BIRMINGHAM) LIMITED", "company_number": "5", "company_status": "active",
               "address_snippet": "Sutton Coldfield B75 7BU", "company_type": "ltd"}]
    assert best_match("Sutton Kitchens", "B75 7BU", sutton)["company_number"] == "5"
    assert best_match("Sutton Kitchens", "LS1 1AA", sutton) is None


def test_run_skips_showrooms_the_cleanse_removed(tmp_path, monkeypatch, capsys):
    from prospector import run
    items = [place(), place(title="Beta Kitchens", website="https://beta-kitchens.co.uk/", phone="01926 000009"),
             place(title="Warwick Kitchen Fitters", website="https://wkf.co.uk/", phone="01926 000008")]
    f = tmp_path / "items.json"
    f.write_text(json.dumps(items))
    towns = tmp_path / "towns.csv"
    towns.write_text("town,region\nWarwick,West Midlands\n")
    monkeypatch.setattr("prospector.location.lookup", lambda pcs, session=None: {})
    monkeypatch.setattr(run, "open_sheet", lambda sheet_id: object())
    monkeypatch.setattr(run, "read_chains", lambda s: [])
    monkeypatch.setattr(run, "read_prospects", lambda s: {r: {f: "" for f in INPUT_COLUMNS} for r in range(4, 10)})
    monkeypatch.setattr(run, "read_removed", lambda s: {"d:acmekitchens.co.uk"})
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "x")
    monkeypatch.setattr("prospector.run.OUTPUT", tmp_path)
    main(["--towns", str(towns), "--maps-json", str(f), "--skip-companies-house", "--skip-meta", "--skip-google",
          "--dry-run"])
    out = capsys.readouterr().out
    assert "1 removed in cleanse" in out and "1 Fitter or builder, no showroom" in out
    rows = list(csv.DictReader(next(tmp_path.glob("prospects-*.csv")).open()))
    assert [r["company"] for r in rows] == ["Beta Kitchens"]
