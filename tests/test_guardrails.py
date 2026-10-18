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
    assert set(w) == {"decline_wipes_points", "first_choice_waitlist", "favorite_first", "safety_option"}
    assert all(x.severity != "block" for x in w.values())


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
    assert suggest_alternatives(maria(), TURN3)[0] == "1838701TWS"   # closest Spanish DLE to Boyle Heights
    assert "1838701TWS" not in suggest_alternatives(maria(), TURN3 + ["1838701TWS"])
    assert suggest_alternatives(maria(current_grade=8, applying_grade=9), TURN3) == []


def test_render_turn3_in_spanish():
    from core.render import render_warnings
    p = maria(would_not_accept=["1823703"])
    text = render_warnings(check_list(p, TURN3, today=TODAY), p)
    for phrase in ("Solo puede poner 3 escuelas magnet", "Markham", "Ponga solo escuelas que sí aceptaría",
                   "Stevenson", "primera opción"):
        assert phrase in text


# --- B10: ranking strategy advice ------------------------------------------------------

def advice(profile: FamilyProfile, codes: list[str]) -> dict:
    return {w.code: w.params for w in check_list(profile, codes, today=TODAY)
            if w.code in ("favorite_first", "safety_option")}


def test_maria_keep_favorite_and_media_is_safer():
    a = advice(maria(), GOLDEN)
    assert a["favorite_first"] == {"first_choice": "1804703"}
    assert a["safety_option"] == {"code": "1804702", "apps_per_opening": 0.67, "listed": True}


def test_safety_never_points_at_the_first_choice():
    a = advice(maria(), ["1804702", "1804703", "1817902"])  # safest program is already 1st
    assert a["favorite_first"]["first_choice"] == "1804702"
    assert a["safety_option"]["code"] == "1817902"           # lowest ratio among the later choices (1.1)


def test_single_magnet_suggests_a_matched_one_with_room_left():
    a = advice(maria(), ["1804703"])
    assert "favorite_first" not in a
    assert a["safety_option"] == {"code": "1804702", "apps_per_opening": 0.67, "listed": False}


def test_doubted_and_over_limit_programs_are_never_the_safety():
    assert advice(maria(would_not_accept=["1804702"]), GOLDEN)["safety_option"]["code"] == "1817902"
    assert advice(maria(), TURN3)["safety_option"]["code"] == "1804702"  # Markham (0.24) is past the limit


def test_no_seat_data_means_no_safety_claim():
    a = advice(FamilyProfile(current_grade=5, interests=["stem"], home_neighborhood="El Sereno"),
               ["1811802", "1811804", "1826402"])  # B9 programs without official seat numbers yet
    assert a == {"favorite_first": {"first_choice": "1811802"}}


def test_threshold_comes_from_config(monkeypatch):
    from core import guardrails
    real = guardrails.load_config
    monkeypatch.setattr(guardrails, "load_config", lambda n: {**real(n), "safety_max_apps_per_opening": 0.5}
                        if n == "competitiveness" else real(n))
    assert "safety_option" not in advice(maria(), GOLDEN)


def test_advice_renders_honestly_in_both_languages():
    from core.render import render_warnings
    for lang, phrase, low in (("es", "Ponga primero la escuela que más quiere", "menos solicitudes que lugares"),
                              ("en", "Keep the school you want most first", "fewer applications than seats")):
        p = maria(language=lang)
        text = render_warnings(check_list(p, GOLDEN, today=TODAY), p)
        assert phrase in text and low in text and "%" not in text and "garantizado" not in text
