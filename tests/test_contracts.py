"""G0 contract tests: the shared types and messages import, validate and round-trip."""
from datetime import date

from agents.messages import PaperworkRequest, PaperworkResponse, PointsRequest, PointsResponse
from core import stubs
from core.types import STAGES, WARNING_CODES, ActionPlan, FamilyProfile, PointsEstimate, Warning


def test_profile_defaults():
    p = FamilyProfile()
    assert p.language == "en" and p.interests == [] and p.waitlist_years == 0


def test_profile_round_trip():
    assert FamilyProfile.model_validate_json(stubs.MARIA.model_dump_json()) == stubs.MARIA


def test_stub_matches_golden_order():
    assert [m.code for m in stubs.match(stubs.MARIA)] == ["1804703", "1817902", "1804702"]


def test_stub_points_maria():
    est = stubs.estimate(stubs.MARIA, {"code": "1804703", "program_type": "magnet"})
    assert est.total == 4 and est.max_possible == 23 and est.lines[0].status == "assumed"
    assert not stubs.estimate(stubs.MARIA, {"code": "1838701TWS", "program_type": "dle"}).applicable


def test_stub_turn3_warnings_use_known_codes():
    codes = ["1804703", "1817902", "1804702", "1823703"]
    warnings = stubs.check_list(stubs.MARIA, codes)
    assert {w.code for w in warnings} >= {"over_limit_magnet", "would_not_accept", "decline_wipes_points",
                                          "first_choice_waitlist"}
    assert all(WARNING_CODES[w.code] == w.severity for w in warnings)
    assert stubs.suggest_alternatives(stubs.MARIA, codes) == ["1838701TWS"]


def test_stub_plan_days_left():
    plan = stubs.plan(stubs.MARIA, ["1804703"], "es", date(2026, 10, 17))
    assert plan.deadlines[0]["days_left"] == 20
    assert ActionPlan.model_validate_json(plan.model_dump_json()) == plan


def test_stages():
    assert STAGES == ("intake", "matched", "odds", "listing", "plan")


def test_messages_carry_json():
    profile_json = stubs.MARIA.model_dump_json()
    req = PointsRequest(session_id="ab12", profile_json=profile_json, codes=["1804703"])
    assert FamilyProfile.model_validate_json(req.profile_json) == stubs.MARIA
    est = stubs.estimate(stubs.MARIA, {"code": "1804703"})
    resp = PointsResponse(session_id="ab12", estimates_json=f"[{est.model_dump_json()}]",
                          warnings_json=f"[{Warning(code='decline_wipes_points', severity='info').model_dump_json()}]")
    assert PointsEstimate.model_validate_json(resp.estimates_json[1:-1]).total == 4
    PaperworkRequest(session_id="ab12", profile_json=profile_json, codes=["1804703"], language="es")
    PaperworkResponse(session_id="ab12", plan_json="{}")
