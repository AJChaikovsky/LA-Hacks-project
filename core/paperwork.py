"""Checklist, deadlines with days left, and phone steps (codes only). Email lives in core/email_draft.py.

Owner: Dev B, ticket B6 (#15). Must match the signature in core/stubs.py where one is listed.

Everything here returns codes; core/render.py localizes them. Coordinator contact is the school phone
already in data/schools.json (no invented emails). Step sources live in config/steps.json.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from core.config import load_config
from core.email_draft import draft_email
from core.guardrails import check_list, today_la
from core.matcher import schools
from core.types import ActionPlan, ChecklistCode, Deadline, FamilyProfile, Language, StepCode


def checklist(profile: FamilyProfile, codes: Sequence[str] = ()) -> list[ChecklistCode]:
    """What to have at hand before opening apply.lausd.net."""
    gifted_codes = {s.code for s in schools() if s.gifted_verification}
    items: list[ChecklistCode] = ["parent_portal_account", "student_id", "address_on_file"]
    if profile.sibling_program_codes:
        items.append("sibling_info")
    if gifted_codes & set(codes):
        items.append("gifted_form")
    if profile.iep:
        items.append("iep_copy")  # for the parent's own reference and the coordinator call; never uploaded
    return items


def all_deadlines(today: date) -> list[Deadline]:
    """Every key date with days left (None when only the month is known), in config order."""
    out = []
    for row in load_config("deadlines")["deadlines"]:
        when = date.fromisoformat(row["date"]) if row["date"] else None
        out.append(Deadline(key=row["key"], date=when, days_left=(when - today).days if when else None))
    return out


def deadlines(today: date) -> list[Deadline]:
    """The dates the plan shows: still-upcoming plan keys; after Nov 6 the late window replaces it."""
    cfg = load_config("deadlines")
    rows = {d.key: d for d in all_deadlines(today)}
    on_time = rows["on_time_deadline"]
    keys = cfg["plan_keys_late"] if on_time.date and today > on_time.date else cfg["plan_keys"]
    return [rows[k] for k in keys if rows[k].days_left is None or rows[k].days_left >= 0]


def phone_steps(codes: Sequence[str] = (), has_siblings: bool = False) -> list[StepCode]:
    """6 to 8 steps on apply.lausd.net; the DLE and sibling steps appear only when they apply."""
    dle = {s.code for s in schools() if s.program_type == "dle"}
    steps: list[StepCode] = ["open_apply_site", "sign_in_or_register", "add_student", "choose_program_type",
                             "rank_magnet_choices"]
    if dle & set(codes):
        steps.append("add_dle_choice")
    if has_siblings:
        steps.append("list_siblings")
    steps += ["check_address", "submit_and_save_confirmation"]
    return steps


def build_plan(profile: FamilyProfile, codes: list[str], lang: Language, today: date | None = None) -> ActionPlan:
    today = today or today_la()
    subject, body = draft_email(profile, codes, lang)
    first = next((s for s in schools() if codes and s.code == codes[0]), None)
    return ActionPlan(
        coordinator_code=first.code if first else None,
        coordinator_phone=first.phone if first else None,
        checklist=checklist(profile, codes),
        deadlines=deadlines(today),
        steps=phone_steps(codes, has_siblings=bool(profile.sibling_program_codes)),
        email_subject=subject,
        email_body=body,
        warnings=check_list(profile, codes, today=today),
    )
