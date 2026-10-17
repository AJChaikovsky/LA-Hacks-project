"""B4 points engine tests: brochure rules, statuses, competitiveness, and Maria's golden estimate."""
from pathlib import Path

import pytest

from core.points import competitiveness, estimate, lookup_resident_school, resident_flags
from core.types import FamilyProfile, School

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = ["1804703", "1817902", "1804702"]
STEAM = "1804703"


def maria() -> FamilyProfile:
    return FamilyProfile.model_validate_json((FIXTURES / "maria_profile.json").read_text(encoding="utf-8"))


def lines(e) -> dict:
    return {line.factor: (line.points, line.status) for line in e.lines}


def one(profile: FamilyProfile, code: str = STEAM):
    return estimate(profile, [code])[0]


# --- Maria (golden turn 2) -------------------------------------------------------------

def test_maria_is_probably_4_of_23():
    for e in estimate(maria(), GOLDEN):
        assert e.applicable and e.total == 4 and e.max_possible == 23
        assert lines(e)["phbao"] == (4, "assumed")          # Hollenbeck inferred from Boyle Heights
        assert lines(e)["overcrowded"] == (0, "assumed")    # official list: Hollenbeck not overcrowded


def test_maria_named_resident_school_is_confirmed():
    p = maria().model_copy(update={"resident_school": "Hollenbeck Middle School"})
    assert lines(one(p))["phbao"] == (4, "confirmed") and lines(one(p))["overcrowded"] == (0, "confirmed")


def test_unknown_resident_school_counts_zero_and_is_unknown():
    p = FamilyProfile(home_neighborhood="El Sereno")
    e = one(p)
    assert lines(e)["phbao"] == (0, "unknown") and lines(e)["overcrowded"] == (0, "unknown") and e.total == 0


def test_parent_stated_flags_win():
    e = one(FamilyProfile(resident_phbao=False, resident_overcrowded=True, home_neighborhood="Boyle Heights"))
    assert lines(e)["phbao"] == (0, "confirmed") and lines(e)["overcrowded"] == (4, "confirmed") and e.total == 4


def test_non_phbao_resident_school():
    e = one(FamilyProfile(resident_school="Playa Del Rey Elementary"))
    assert lines(e)["phbao"] == (0, "confirmed")


# --- Brochure rules --------------------------------------------------------------------

@pytest.mark.parametrize("years,expected", [(0, 0), (1, 4), (2, 8), (3, 12), (5, 12)])
def test_waitlist_is_4_per_year_capped_at_12(years, expected):
    assert lines(one(FamilyProfile(waitlist_years=years)))["waitlist"][0] == expected


def test_matriculation_drops_waitlist():
    e = one(FamilyProfile(matriculating=True, waitlist_years=3))
    assert lines(e)["matriculation"] == (12, "confirmed") and lines(e)["waitlist"] == (0, "confirmed")
    assert e.total == 12


def test_sibling_only_on_first_choice():
    p = FamilyProfile(sibling_program_codes=["1817902"])
    first, second = estimate(p, ["1817902", "1804703"])
    assert lines(first)["sibling"][0] == 3
    second_choice = estimate(p, ["1804703", "1817902"])[1]
    assert second_choice.code == "1817902" and lines(second_choice)["sibling"][0] == 0
    assert lines(second)["sibling"][0] == 0


def test_dle_is_not_applicable():
    e = one(maria(), "1838701TWS")
    assert not e.applicable and e.total == 0 and e.lines == []


def test_max_is_23():
    p = FamilyProfile(matriculating=True, waitlist_years=3, resident_phbao=True, resident_overcrowded=True,
                      sibling_program_codes=[STEAM])
    e = one(p)
    assert e.total == 23 == e.max_possible


def test_late_application_earns_nothing():
    p = FamilyProfile(late_application=True, waitlist_years=3, resident_phbao=True, sibling_program_codes=[STEAM])
    e = one(p)
    assert e.total == 0 and all(line.points == 0 for line in e.lines)


def test_order_and_unknown_codes():
    assert [e.code for e in estimate(maria(), ["1817902", "nope", STEAM])] == ["1817902", STEAM]
    assert estimate(maria(), []) == []


# --- Resident school lookup ------------------------------------------------------------

@pytest.mark.parametrize("name", ["Hollenbeck Middle School", "Hollenbeck MS", "hollenbeck", "Hollenbeck Middle"])
def test_lookup_resident_school_variants(name):
    assert lookup_resident_school(name)["cost_center"] == "1817901"


def test_lookup_ambiguous_or_missing_returns_none():
    assert lookup_resident_school("Belvedere") is None   # Belvedere El and Belvedere MS
    assert lookup_resident_school("Nowhere Academy") is None
    assert lookup_resident_school("2nd Street Elementary")["name"] == "2nd St El"


def test_resident_flags_for_maria():
    assert resident_flags(maria()) == {"phbao": (True, "assumed"), "overcrowded": (False, "assumed")}


# --- Competitiveness -------------------------------------------------------------------

def _school(apps, openings) -> School:
    return School(code="x", name="x", program_type="magnet", theme="STEAM", grades=[6], address="a", zip="9",
                  source_url="https://x", applications_2025_26=apps, openings_2026_27=openings)


@pytest.mark.parametrize("apps,openings,band,ratio", [
    (500, 100, "very_high", 5.0), (300, 100, "high", 3.0), (150, 100, "moderate", 1.5),
    (None, 100, "unknown", None), (500, None, "unknown", None), (50, 0, "very_high", None)])
def test_competitiveness_bands(apps, openings, band, ratio):
    assert competitiveness(_school(apps, openings)) == (band, ratio)


def test_seed_programs_have_no_odds_until_b7():
    assert all(e.competitiveness == "unknown" and e.apps_per_opening is None for e in estimate(maria(), GOLDEN))


def test_render_says_probably_4_of_23():
    from core.render import render_points
    text = render_points(estimate(maria(), GOLDEN), maria())
    assert "probablemente 4 de 23 puntos" in text and "%" not in text
