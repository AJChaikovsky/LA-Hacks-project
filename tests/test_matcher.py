"""B3 matcher tests: Maria's golden top 3, five more profiles, and the grade filter."""
import json
from pathlib import Path

import pytest

from core.matcher import eligible, haversine_km, match, schools
from core.types import FamilyProfile

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def maria() -> FamilyProfile:
    return FamilyProfile.model_validate_json((FIXTURES / "maria_profile.json").read_text(encoding="utf-8"))


def codes(profile: FamilyProfile) -> list[str]:
    return [m.code for m in match(profile)]


def test_maria():
    result = match(maria())
    assert [m.code for m in result] == ["1804703", "1817902", "1804702"]
    steam, hollenbeck, media = result
    assert steam.reason_codes[:2] == ["no_vapa_nearby", "arts_in_steam"]
    assert "near_home" in hollenbeck.reason_codes
    assert "arts_in_media" in media.reason_codes
    assert all("iep_eligible" in m.reason_codes for m in result)
    assert hollenbeck.distance_km < steam.distance_km and steam.address.startswith("312 N Record Ave")


def test_maria_formula_without_near_home_slot(monkeypatch):
    """The pure formula ranks Griffith STEAM 3rd; the near-home slot is what brings Hollenbeck in."""
    from core import matcher
    cfg = json.loads(json.dumps(matcher.load_config("matcher")))
    cfg["near_home_slot"]["enabled"] = False
    monkeypatch.setattr(matcher, "load_config", lambda name: cfg if name == "matcher" else matcher.load_config(name))
    assert codes(maria()) == ["1804703", "1804702", "1816801"]


def test_stem_kid_near_downtown_gets_robotics():
    p = FamilyProfile(language="en", current_grade=5, interests=["stem"], home_neighborhood="Pico-Union",
                      parent_work_area="Downtown")
    result = match(p)
    assert result[0].code == "1800903" and "near_work" in result[0].reason_codes


def test_spanish_dle_family():
    p = FamilyProfile(language="es", current_grade=5, interests=["languages"], home_language="Spanish",
                      home_neighborhood="East Los Angeles")
    top = match(p)[0]
    assert top.code == "1838701TWS" and top.program_type == "dle"
    assert {"theme_match", "spanish_dle"} <= set(top.reason_codes)
    assert "iep_eligible" not in top.reason_codes


def test_arts_kid_in_south_la_has_vapa_nearby():
    p = FamilyProfile(current_grade=5, interests=["arts"], home_neighborhood="South LA")
    result = match(p)
    assert result[0].code == "1823703" and "theme_match" in result[0].reason_codes
    assert not any("no_vapa_nearby" in m.reason_codes for m in result)


def test_gifted_program_only_for_identified_gifted():
    base = dict(current_grade=5, interests=[], home_neighborhood="Boyle Heights")
    gifted_program = next(s for s in schools() if s.code == "1838702")
    for gifted in (None, False):
        assert not eligible(FamilyProfile(**base, gifted=gifted), gifted_program)
        assert "1838702" not in codes(FamilyProfile(**base, gifted=gifted))
    assert eligible(FamilyProfile(**base, gifted=True), gifted_program)


def test_no_profile_is_deterministic():
    assert codes(FamilyProfile()) == codes(FamilyProfile()) and len(codes(FamilyProfile())) == 3
    # equal scores break ties by code
    result = match(FamilyProfile())
    tied = [m.code for m in result if m.score == result[0].score]
    assert tied == sorted(tied)


def test_applying_grade_without_programs_returns_nothing():
    assert match(FamilyProfile(current_grade=8, interests=["arts"], home_neighborhood="Boyle Heights")) == []


@pytest.mark.parametrize("grade", range(0, 13))
def test_never_outside_applying_grade(grade):
    by_code = {s.code: s for s in schools()}
    for m in match(FamilyProfile(applying_grade=grade, interests=["arts", "stem"], gifted=True)):
        assert grade in by_code[m.code].grades


def test_haversine_known_distance():
    # Hollenbeck MS to Belvedere MS is about 2.8 km
    assert 2.5 < haversine_km(34.03613, -118.21218, 34.03947, -118.18216) < 3.1


def test_stem_interest_gets_theme_match_on_strong_fit():
    from core.matcher import match
    from core.types import FamilyProfile

    ana = FamilyProfile(language="en", child_name="Ana", current_grade=5, interests=["stem"],
                        home_neighborhood="El Sereno", iep=False)
    result = match(ana)
    assert result and all("theme_match" in m.reason_codes for m in result)
