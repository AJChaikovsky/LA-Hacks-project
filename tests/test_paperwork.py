"""B6 paperwork tests: checklist, deadlines (days left in LA time), phone steps with sources, the plan."""
import json
from datetime import date
from pathlib import Path

import pytest

from core.config import load_config
from core.paperwork import all_deadlines, build_plan, checklist, deadlines, phone_steps
from core.types import STEP_CODES, ActionPlan, FamilyProfile

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OCT17 = date(2026, 10, 17)
GOLDEN = ["1804703", "1817902", "1804702"]


@pytest.fixture(autouse=True)
def offline_email(monkeypatch):
    monkeypatch.setenv("EMAIL_LLM", "off")
    monkeypatch.setenv("DEMO_MODE", "false")


def maria(**update) -> FamilyProfile:
    p = FamilyProfile.model_validate_json((FIXTURES / "maria_profile.json").read_text(encoding="utf-8"))
    return p.model_copy(update=update)


def test_maria_plan_nov6_is_20_days():
    plan = build_plan(maria(), GOLDEN, "es", OCT17)
    first = plan.deadlines[0]
    assert (first.key, first.date, first.days_left) == ("on_time_deadline", date(2026, 11, 6), 20)


def test_maria_plan_matches_fixture():
    fixture = ActionPlan.model_validate_json((FIXTURES / "maria_plan.json").read_text(encoding="utf-8"))
    plan = build_plan(maria(), GOLDEN, "es", OCT17)
    assert plan.checklist == fixture.checklist
    assert plan.deadlines == fixture.deadlines
    assert plan.steps == fixture.steps
    assert plan.email_subject and "IEP" in plan.email_body
    assert {w.code for w in plan.warnings} == {"decline_wipes_points", "first_choice_waitlist"}


def test_checklist_conditional_items():
    assert checklist(FamilyProfile()) == ["parent_portal_account", "student_id", "address_on_file"]
    assert "iep_copy" in checklist(maria())
    assert "sibling_info" in checklist(FamilyProfile(sibling_program_codes=["1817902"]))
    assert "gifted_form" in checklist(FamilyProfile(), ["1838702"])
    assert "gifted_form" not in checklist(FamilyProfile(), GOLDEN)


def test_all_deadlines_days_left():
    rows = {d.key: d for d in all_deadlines(OCT17)}
    assert rows["on_time_open"].days_left == -16
    assert rows["correction_deadline"].days_left == 62
    assert rows["accept_decline"].days_left == 139
    assert rows["confirmation_notice"].date is None and rows["confirmation_notice"].days_left is None


def test_deadline_day_of_is_zero_and_kept():
    assert deadlines(date(2026, 11, 6))[0].days_left == 0


def test_after_nov6_shows_late_window():
    keys = [d.key for d in deadlines(date(2026, 11, 20))]
    assert keys[0] == "late_window_opens" and "on_time_deadline" not in keys


def test_past_dates_drop_off():
    keys = [d.key for d in deadlines(date(2027, 1, 10))]
    assert "correction_deadline" not in keys and "accept_decline" in keys


def test_phone_steps_6_to_8():
    for codes, siblings in [(GOLDEN, False), (GOLDEN + ["1838701TWS"], True), ([], False)]:
        steps = phone_steps(codes, has_siblings=siblings)
        assert 6 <= len(steps) <= 9 and steps[0] == "open_apply_site" and steps[-1] == "submit_and_save_confirmation"
    assert "add_dle_choice" in phone_steps(GOLDEN + ["1838701TWS"])
    assert "list_siblings" in phone_steps(GOLDEN, has_siblings=True)
    assert len(phone_steps(GOLDEN)) == 7


def test_every_step_has_a_source():
    cfg = load_config("steps")
    rows = {s["code"]: s for s in cfg["steps"]}
    assert set(rows) == set(STEP_CODES)
    for s in rows.values():
        assert s["source"].startswith("https://") and s["status"] in ("confirmed", "unconfirmed")
        assert s["status"] == "unconfirmed" or s["quote"]


def test_plan_round_trips_as_json():
    plan = build_plan(maria(), GOLDEN, "es", OCT17)
    assert ActionPlan.model_validate_json(plan.model_dump_json()) == plan
    json.loads(plan.model_dump_json())


def test_render_turn4_in_spanish():
    from core.render import render_plan
    text = render_plan(build_plan(maria(), GOLDEN, "es", OCT17), maria())
    for phrase in ("6 de noviembre de 2026", "faltan 20 días", "apply.lausd.net", "Parent Portal", "IEP", "Asunto:",
                   "Usted envía la solicitud; nosotros no enviamos nada."):
        assert phrase in text


def test_coordinator_phone_is_first_choice_school_phone():
    plan = build_plan(maria(), GOLDEN, "es", OCT17)
    assert (plan.coordinator_code, plan.coordinator_phone) == ("1804703", "(323) 266-5400")
    assert build_plan(maria(), [], "es", OCT17).coordinator_phone is None


def test_phone_shows_in_reply_and_review_card():
    from agents.cards import packet_review, validate
    from core.render import render_plan
    plan = build_plan(maria(), GOLDEN, "es", OCT17)
    assert "Si prefiere llamar: Belvedere MS · STEAM, (323) 266-5400." in render_plan(plan, maria())
    card = packet_review(plan, maria(), GOLDEN)
    assert {"label": "Teléfono", "value": "(323) 266-5400"} in card["summary_rows"]
    assert validate("review", card) == []
    en = maria(language="en")
    assert "Prefer to call? " in render_plan(build_plan(en, GOLDEN, "en", OCT17), en)
