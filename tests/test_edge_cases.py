"""B8 edge cases: unusual families run through match -> points -> guardrails -> plan -> render without breaking."""
from datetime import date

import pytest

from core.guardrails import check_list
from core.matcher import match, schools
from core.paperwork import build_plan
from core.points import estimate
from core.render import render_matches, render_plan, render_points, render_warnings
from core.types import FamilyProfile

TODAY = date(2026, 10, 17)
PROFILES = {
    "english_only": FamilyProfile(language="en", current_grade=5, interests=["arts"], home_language="English",
                                  home_neighborhood="Boyle Heights"),
    "missing_grade": FamilyProfile(language="es", interests=["arts"], home_neighborhood="Boyle Heights"),
    "grade_without_programs": FamilyProfile(language="es", current_grade=8, interests=["arts"],
                                            home_neighborhood="Boyle Heights"),
    "outside_lausd": FamilyProfile(language="en", current_grade=5, interests=["stem"], home_neighborhood="Pasadena",
                                   parent_work_area="Burbank"),
    "fourth_grader": FamilyProfile(language="es", current_grade=4, interests=["arts"],
                                   home_neighborhood="Boyle Heights"),
    "only_dle": FamilyProfile(language="es", current_grade=5, interests=["languages"], home_language="Spanish",
                              english_learner=True, home_neighborhood="Boyle Heights"),
    "unknown_place": FamilyProfile(language="en", current_grade=5, interests=["arts"], home_neighborhood="Atlantis"),
    "empty": FamilyProfile(),
}


@pytest.fixture(autouse=True)
def offline_email(monkeypatch):
    monkeypatch.setenv("EMAIL_LLM", "off")
    monkeypatch.setenv("DEMO_MODE", "false")


@pytest.mark.parametrize("name", PROFILES)
def test_full_pipeline_never_breaks(name):
    p = PROFILES[name]
    matches = match(p)
    codes = [m.code for m in matches]
    estimates, warnings = estimate(p, codes), check_list(p, codes, today=TODAY)
    plan = build_plan(p, codes, p.language, TODAY)
    for text in (render_matches(matches, p), render_points(estimates, p), render_warnings(warnings, p),
                 render_plan(plan, p)):
        assert text and "{" not in text and "%" not in text and "garantizado" not in text


def test_english_only_gets_no_spanish_dle_push():
    p = PROFILES["english_only"]
    assert "1838701TWS" not in [m.code for m in match(p)]
    assert all(w.code != "not_points_program" for w in check_list(p, [m.code for m in match(p)], today=TODAY))


def test_missing_grade_still_matches_middle_school_programs():
    assert [m.code for m in match(PROFILES["missing_grade"])] == ["1804703", "1817902", "1816801"]


@pytest.mark.parametrize("name", ["grade_without_programs", "fourth_grader"])
def test_no_programs_for_the_grade(name):
    p = PROFILES[name]
    assert match(p) == []
    assert estimate(p, []) == [] and check_list(p, [], today=TODAY) == []
    assert build_plan(p, [], p.language, TODAY).coordinator_phone is None


def test_outside_lausd_earns_no_points_and_is_warned():
    p = PROFILES["outside_lausd"]
    codes = [m.code for m in match(p)]
    assert codes  # out-of-district families can still apply
    for e in estimate(p, codes):
        assert e.total == 0 and all(line.status == "confirmed" and line.points == 0 for line in e.lines)
    w = {x.code: x for x in check_list(p, codes, today=TODAY)}
    assert w["outside_district"].severity == "warn" and w["outside_district"].params == {"district": "Pasadena Unified"}
    text = render_warnings(list(w.values()), p)
    assert "You live outside LAUSD (Pasadena Unified)" in text


def test_lausd_family_gets_no_outside_warning():
    p = FamilyProfile(current_grade=5, interests=["arts"], home_neighborhood="Boyle Heights")
    assert "outside_district" not in {w.code for w in check_list(p, ["1804703"], today=TODAY)}


def test_only_dle_plan_has_no_magnet_steps():
    p = PROFILES["only_dle"]
    assert match(p)[0].code == "1838701TWS"
    plan = build_plan(p, ["1838701TWS"], "es", TODAY)
    assert "rank_magnet_choices" not in plan.steps
    assert plan.steps == ["open_apply_site", "sign_in_or_register", "add_student", "check_address",
                          "choose_program_type", "add_dle_choice", "submit_and_save_confirmation"]
    assert not estimate(p, ["1838701TWS"])[0].applicable
    assert {w.code for w in check_list(p, ["1838701TWS"], today=TODAY)} == {"not_points_program"}


def test_unknown_place_falls_back_without_distance():
    result = match(PROFILES["unknown_place"])
    assert len(result) == 3 and all(m.distance_km is None for m in result)


@pytest.mark.parametrize("grade", range(-1, 14))
@pytest.mark.parametrize("interests", [[], ["arts"], ["stem", "medicine"], ["languages"], ["sports"]])
def test_property_matcher_never_returns_program_outside_grade(grade, interests):
    by_code = {s.code: s for s in schools()}
    p = FamilyProfile(current_grade=grade, interests=interests, gifted=True, home_language="Spanish",
                      home_neighborhood="Boyle Heights")
    assert all(grade + 1 in by_code[m.code].grades for m in match(p))
