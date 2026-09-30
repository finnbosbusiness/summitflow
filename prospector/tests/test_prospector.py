import json

from prospector import maps
from prospector.chains import FALLBACK_CHAINS, is_chain
from prospector.columns import FORMULA_COLUMNS, INPUT_COLUMNS, contiguous_blocks
from prospector.companies_house import best_match, format_officer_name, pick_director
from prospector.matching import domain, prospect_key
from prospector.run import main
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


def test_to_prospect_uses_searched_town():
    p = maps.to_prospect(place(city="Royal Leamington Spa", searchString="kitchen showroom Leamington Spa"), TOWNS)
    assert p["town"] == "Leamington Spa"
    assert p["region"] == "West Midlands"
    assert p["physical_showroom"] == "Y"
    assert p["source"] == "Google Maps"


def test_to_prospect_filters():
    assert maps.to_prospect(place(title="Bob's Plumbing", categoryName="Plumber", categories=[]), TOWNS) is None
    assert maps.to_prospect(place(permanentlyClosed=True), TOWNS) is None
    remodeler = maps.to_prospect(place(categoryName="Kitchen remodeler", categories=[]), TOWNS)
    assert remodeler["physical_showroom"] == ""
    assert maps.to_prospect(place(website="https://facebook.com/acme"), TOWNS)["website"] == ""


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


def test_director():
    officers = [
        {"name": "JONES, Mary", "officer_role": "secretary", "appointed_on": "2001-01-01"},
        {"name": "SMITH, Mr John Paul", "officer_role": "director", "appointed_on": "2010-05-01"},
        {"name": "BROWN, Alan", "officer_role": "director", "appointed_on": "2005-01-01", "resigned_on": "2012-01-01"},
        {"name": "GREEN, Sue", "officer_role": "director", "appointed_on": "2018-01-01"},
    ]
    assert pick_director(officers) == "John Paul Smith"
    assert format_officer_name("ACME HOLDINGS LIMITED") == "ACME HOLDINGS LIMITED"


def test_dry_run_end_to_end(tmp_path, monkeypatch, capsys):
    items = [
        place(),
        place(searchString="kitchen showroom Leamington Spa"),  # duplicate in second town
        place(title="Howdens", website="https://howdens.com"),
        place(title="Beta Kitchen Design", website="", postalCode="CV32 5AA", searchString="kitchen showroom Leamington Spa"),
    ]
    f = tmp_path / "items.json"
    f.write_text(json.dumps(items))
    towns = tmp_path / "towns.csv"
    towns.write_text("town,region\nWarwick,West Midlands\nLeamington Spa,West Midlands\n")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr("prospector.run.OUTPUT", tmp_path)
    main(["--towns", str(towns), "--maps-json", str(f), "--skip-companies-house", "--dry-run"])
    out = capsys.readouterr().out
    assert "2 independent kitchen businesses, 1 chains skipped" in out
    csv_text = next(tmp_path.glob("prospects-*.csv")).read_text()
    assert "Acme Kitchens" in csv_text and "Beta Kitchen Design" in csv_text and "Howdens" not in csv_text
