"""B2 config tests: files load and match the brochure's points table and key dates."""
from datetime import date

from core.config import load_config
from core.types import DEADLINE_KEYS, MAX_MAGNET_POINTS


def test_points_match_brochure_table():
    cfg = load_config("points")
    f = cfg["factors"]
    assert f["matriculation"]["points"] == 12
    assert f["waitlist"]["points"] == 4 and f["waitlist"]["max_years"] == 3 and f["waitlist"]["cap"] == 12
    assert f["phbao"]["points"] == 4 and f["overcrowded"]["points"] == 4 and f["sibling"]["points"] == 3
    assert cfg["max_total"] == MAX_MAGNET_POINTS == 23
    assert cfg["late_or_invalid_points"] == 0


def test_points_rules():
    f = load_config("points")["factors"]
    assert "waitlist" not in f["matriculation"]["combinable_with"]
    assert "matriculation" not in f["waitlist"]["combinable_with"]
    assert [k for k, v in f.items() if v["first_choice_only"]] == ["sibling"]
    # max = matriculation (larger of the exclusive pair) + phbao + overcrowded + sibling
    best = max(f["matriculation"]["cap"], f["waitlist"]["cap"]) + 4 + 4 + 3
    assert best == load_config("points")["max_total"]
    assert all(v["source"].startswith("https://") for v in f.values())


def test_deadlines_cover_every_key():
    cfg = load_config("deadlines")
    assert cfg["timezone"] == "America/Los_Angeles"
    rows = {d["key"]: d for d in cfg["deadlines"]}
    assert tuple(rows) == DEADLINE_KEYS
    assert rows["on_time_deadline"]["date"] == "2026-11-06"
    assert (date.fromisoformat(rows["on_time_deadline"]["date"]) - date(2026, 10, 17)).days == 20
    assert rows["accept_decline"]["date"] == "2027-03-05"
    for d in rows.values():
        assert (d["date"] is None) != (d["month"] is None), d["key"]
        assert d["label_es"] and d["label_en"] and d["source"].startswith("https://")


def test_limits():
    assert load_config("limits") == {
        "magnet_max_choices": 3, "program_types_max": 6, "emc_max": 1, "bus_radius_miles_secondary": 2,
        "max_ride_minutes": 90, "source": "https://choices.lausd.net/Brochure/Brochure_EN.pdf?27"}


def test_competitiveness_bands_descend():
    cfg = load_config("competitiveness")
    assert cfg["heuristic"] is True
    mins = [b["min_apps_per_opening"] for b in cfg["bands"]]
    assert mins == sorted(mins, reverse=True) and mins[-1] == 0
    assert [b["band"] for b in cfg["bands"]] == ["very_high", "high", "moderate", "low"]
