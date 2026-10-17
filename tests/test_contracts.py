"""Gate G0 checks: fixtures fit the frozen types, stubs honor the interface, messages round-trip."""
import json
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter

from agents.messages import PaperworkRequest, PaperworkResponse, PointsRequest, PointsResponse
from core import stubs
from core.types import (
    INTENTS,
    STAGES,
    ActionPlan,
    FamilyProfile,
    GuardrailWarning,
    PointsEstimate,
    SchoolMatch,
    SessionState,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_fixtures_validate_against_types():
    # Literal-typed fields reject any code that isn't in the frozen lists.
    FamilyProfile.model_validate(load("maria_profile.json"))
    TypeAdapter(list[SchoolMatch]).validate_python(load("maria_matches.json"))
    TypeAdapter(list[PointsEstimate]).validate_python(load("maria_estimates.json"))
    TypeAdapter(list[GuardrailWarning]).validate_python(load("maria_warnings.json"))
    ActionPlan.model_validate(load("maria_plan.json"))


def test_maria_profile_is_the_demo_persona():
    p = FamilyProfile.model_validate(load("maria_profile.json"))
    assert (p.language, p.current_grade, p.applying_grade) == ("es", 5, 6)
    assert p.iep is True and "arts" in p.interests
    assert p.home_neighborhood == "Boyle Heights"


def test_estimates_cover_every_matched_magnet():
    matches = TypeAdapter(list[SchoolMatch]).validate_python(load("maria_matches.json"))
    estimates = TypeAdapter(list[PointsEstimate]).validate_python(load("maria_estimates.json"))
    assert {m.code for m in matches if m.program_type == "magnet"} == {e.code for e in estimates}
    for e in estimates:
        assert e.total == sum(line.points for line in e.lines) <= e.max_possible


def test_deadline_days_left_match_dates():
    plan = ActionPlan.model_validate(load("maria_plan.json"))
    for d in plan.deadlines:
        if d.date is not None:
            assert d.days_left == (d.date - date(2026, 10, 17)).days


def test_stubs_return_valid_models():
    profile = stubs.extract("Hola, soy Maria...")
    assert isinstance(profile, FamilyProfile)
    matches = stubs.match(profile)
    codes = [m.code for m in matches]
    assert len(matches) == 3
    assert all(isinstance(e, PointsEstimate) for e in stubs.estimate(profile, codes))
    assert all(isinstance(w, GuardrailWarning) for w in stubs.check_list(profile, codes))
    assert isinstance(stubs.build_plan(profile, codes, "es", date(2026, 10, 17)), ActionPlan)
    for render, items in [
        (stubs.render_matches, matches),
        (stubs.render_points, stubs.estimate(profile, codes)),
        (stubs.render_warnings, stubs.check_list(profile, codes)),
    ]:
        assert render(items, profile).strip()


def test_stub_classify_walks_the_golden_path():
    assert [stubs.classify("", s) for s in STAGES[:4]] == ["profile_info", "ask_odds", "set_list", "ask_plan"]
    assert all(stubs.classify("", s) in INTENTS for s in STAGES)


def test_session_state_round_trips_json():
    state = SessionState(profile=stubs.extract(""), matches=stubs.match(FamilyProfile()))
    assert SessionState.model_validate_json(state.model_dump_json()) == state


def test_agent_messages_round_trip():
    profile = stubs.extract("")
    plan = stubs.build_plan(profile, [], "es", date(2026, 10, 17))
    req = PointsRequest(session_id="s1", profile_json=profile.model_dump_json(), codes=["1804703"])
    assert FamilyProfile.model_validate_json(PointsRequest.parse_raw(req.json()).profile_json) == profile
    resp = PaperworkResponse(session_id="s1", plan_json=plan.model_dump_json())
    assert ActionPlan.model_validate_json(resp.plan_json) == plan
    PointsResponse(session_id="s1", estimates_json="[]", warnings_json="[]")
    PaperworkRequest(session_id="s1", profile_json="{}", codes=[], language="es")
