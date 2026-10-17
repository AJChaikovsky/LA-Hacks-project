"""B5 guardrail tests: golden turn 3 plus each warning code."""
from datetime import date
from pathlib import Path

from core.guardrails import check_list, suggest_alternatives
from core.types import WARNING_CODES, FamilyProfile

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 10, 17)
GOLDEN = ["1804703", "1817902", "1804702"]
TURN3 = GOLDEN + ["1823703"]


def maria(**update) -> FamilyProfile:
    p = FamilyProfile.model_validate_json((FIXTURES / "maria_profile.json").read_text(encoding="utf-8"))
    return p.model_copy(update=update)


def by_code(profile: FamilyProfile, codes: list[str], today: date = TODAY) -> dict:
    return {w.code: w for w in check_list(profile, codes, today=today)}


def test_golden_turn3():
    w = by_code(maria(would_not_accept=["1823703"]), TURN3)
    assert w["over_limit_magnet"].severity == "block"
    assert w["over_limit_magnet"].params == {"codes": ["1823703"], "limit": 3}
    assert w["would_not_accept"].params["codes"] == ["1823703"]
    assert "decline_wipes_points" in w
    assert w["first_choice_waitlist"].params["first_choice"] == "1804703"
    assert w["not_points_program"].params["suggest"] == "1838701TWS"


def test_golden_turn3_matches_fixture_codes():
    import json
    fixture = json.loads((FIXTURES / "maria_warnings.json").read_text(encoding="utf-8"))
    got = {w.code for w in check_list(maria(would_not_accept=["1823703"]), TURN3, today=TODAY)}
    assert {f["code"] for f in fixture} <= got


def test_golden_turn4_list_has_no_block():
    w = by_code(maria(), GOLDEN)
    assert set(w) == {"decline_wipes_points", "first_choice_waitlist"}


def test_severities_match_contract():
    p = maria(would_not_accept=["1823703"], in_magnet_now=True, gifted=True)
    for w in check_list(p, TURN3 + ["1838702", "1838701TWS"], today=date(2026, 12, 1)):
        assert w.code in WARNING_CODES


def test_empty_list_has_no_warnings():
    assert check_list(maria(), [], today=TODAY) == []


def test_dle_does_not_use_a_magnet_slot():
    w = by_code(maria(), GOLDEN + ["1838701TWS"])
    assert "over_limit_magnet" not in w
    assert w["not_points_program"].params == {"codes": ["1838701TWS"], "suggest": "1838701TWS"}


def test_over_limit_names_every_extra_magnet():
    w = by_code(maria(), TURN3 + ["1817903"])
    assert w["over_limit_magnet"].params["codes"] == ["1823703", "1817903"]


def test_first_choice_is_first_magnet():
    w = by_code(maria(), ["1838701TWS", "1817902", "1804703"])
    assert w["first_choice_waitlist"].params["first_choice"] == "1817902"


def test_gifted_verification():
    assert by_code(maria(gifted=True), ["1838702"])["gifted_verification"].params["codes"] == ["1838702"]


def test_far_commute_only_for_far_schools():
    w = by_code(maria(), ["1817902", "1823703"])
    assert w["far_commute"].params["codes"] == ["1823703"]   # Markham 11.8 km, no confirmed bus
    assert "far_commute" not in by_code(maria(), GOLDEN)      # all under 2 miles from Boyle Heights
    assert "far_commute" not in by_code(FamilyProfile(), ["1823703"])  # unknown home: don't guess


def test_current_magnet_drop():
    assert "current_magnet_drop" in by_code(maria(in_magnet_now=True), GOLDEN)
    assert "current_magnet_drop" not in by_code(maria(in_magnet_now=True), ["1838701TWS"])


def test_past_deadline():
    assert "past_deadline" not in by_code(maria(), GOLDEN, today=date(2026, 11, 6))
    assert by_code(maria(), GOLDEN, today=date(2026, 11, 7))["past_deadline"].severity == "warn"
    assert "past_deadline" in by_code(maria(late_application=True), GOLDEN)


def test_suggest_alternatives_prefers_home_language_dle():
    assert suggest_alternatives(maria(), TURN3) == ["1838701TWS"]
    assert suggest_alternatives(maria(), TURN3 + ["1838701TWS"]) == []
    assert suggest_alternatives(maria(current_grade=8, applying_grade=9), TURN3) == []


def test_render_turn3_in_spanish():
    from core.render import render_warnings
    p = maria(would_not_accept=["1823703"])
    text = render_warnings(check_list(p, TURN3, today=TODAY), p)
    for phrase in ("Solo puede poner 3 escuelas magnet", "Markham", "Ponga solo escuelas que sí aceptaría",
                   "Stevenson", "primera opción"):
        assert phrase in text
