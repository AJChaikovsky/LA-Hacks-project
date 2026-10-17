"""B1 schema tests: data/schools.json and data/places.json load and every number is sourced."""
import json
from pathlib import Path

from core.types import School

DATA = Path(__file__).resolve().parent.parent / "data"
SEED_CODES = {"1804703", "1804702", "1817902", "1817903", "1838702", "1838701TWS", "1816801", "1820002",
              "1800903", "1823703"}
NUMERIC_FIELDS = ("lat", "lng", "capacity_2026_27", "applications_2025_26", "openings_2026_27")


def _raw_schools() -> list[dict]:
    return json.loads((DATA / "schools.json").read_text(encoding="utf-8"))


def test_every_record_loads_as_school():
    schools = [School.model_validate(r) for r in _raw_schools()]
    assert {s.code for s in schools} == SEED_CODES
    assert len(schools) == len(SEED_CODES)


def test_every_non_null_number_has_a_source():
    for r in _raw_schools():
        assert r["source_url"].startswith("https://")
        for field in NUMERIC_FIELDS:
            if r[field] is not None:
                src = r.get("field_sources", {}).get(field) or r["source_url"]
                assert src.startswith("https://"), (r["code"], field)
        if r["phone"] is not None:
            assert r["field_sources"]["phone"].startswith("https://"), r["code"]


def test_seed_facts():
    by_code = {r["code"]: School.model_validate(r) for r in _raw_schools()}
    assert by_code["1838701TWS"].program_type == "dle"
    assert [c for c, s in by_code.items() if s.gifted_verification] == ["1838702"]
    assert by_code["1823703"].theme == "VAPA"
    assert all(6 in s.grades for s in by_code.values())
    # every school sits inside the LA basin
    assert all(33.8 < s.lat < 34.2 and -118.4 < s.lng < -118.0 for s in by_code.values())


def test_places_centroids():
    places = json.loads((DATA / "places.json").read_text(encoding="utf-8"))
    assert {"Boyle Heights", "Downtown", "East Los Angeles", "El Sereno", "Lincoln Heights"} <= set(places["neighborhoods"])
    for p in places["neighborhoods"].values():
        assert 33.7 < p["lat"] < 34.4 and -118.7 < p["lng"] < -117.9 and p["aliases"]  # LA County
    plaza = places["landmarks"]["Mariachi Plaza"]
    # resident-school flags stay null unless an official source backs them
    for flag in ("resident_phbao", "resident_overcrowded"):
        assert plaza[flag] is None or (DATA.parent / plaza["resident_flags_source"]).exists()


def test_resident_school_list():
    norm = json.loads((DATA / "resident_schools.json").read_text(encoding="utf-8"))
    assert norm["applies_to_school_year"] == "2027-28" and (DATA.parent / norm["evidence"]).exists()
    by_name = {s["name"]: s for s in norm["schools"]}
    assert len(norm["schools"]) == 697
    assert by_name["Hollenbeck MS"] == {"cost_center": "1817901", "location_code": "8179", "name": "Hollenbeck MS",
                                        "phbao": True, "overcrowded": False}
    assert sorted(s["name"] for s in norm["schools"] if s["overcrowded"]) == [
        "Dahlia Hts El", "Ivanhoe El", "Mount Washington El"]


def test_every_cited_evidence_file_exists():
    for r in _raw_schools():
        ev = r.get("field_sources", {}).get("evidence")
        if r["verified"]:
            assert ev and (DATA.parent / ev).exists(), r["code"]
