"""Placeholder implementations that return the Maria fixtures.

These signatures are the FROZEN interface (gate G0). Each real module must expose a
function with the same name and signature:

    core/extract.py     extract(text, prior)            -> FamilyProfile        (Dev C, C1)
    core/intent.py      classify(text, stage)           -> Intent               (Dev C, C2)
    core/matcher.py     match(profile)                  -> list[SchoolMatch]    (Dev B, B3)
    core/points.py      estimate(profile, codes)        -> list[PointsEstimate] (Dev B, B4)
    core/guardrails.py  check_list(profile, codes)      -> list[GuardrailWarning] (Dev B, B5)
    core/paperwork.py   build_plan(profile, codes, lang, today) -> ActionPlan   (Dev B, B6 + C5)
    core/render.py      render_matches / render_points / render_warnings / render_plan
                        (items, profile) -> str                                 (Dev C, C3)

The orchestrator imports from here while USE_STUBS=true, so agent work never waits on
the real modules. Stubs ignore their inputs on purpose.
"""
from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path

from core.types import (
    ActionPlan,
    FamilyProfile,
    GuardrailWarning,
    Intent,
    Language,
    PointsEstimate,
    SchoolMatch,
    Stage,
)

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


@cache
def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def extract(text: str, prior: FamilyProfile | None = None) -> FamilyProfile:
    return FamilyProfile.model_validate(_load("maria_profile.json"))


_NEXT_INTENT: dict[str, Intent] = {
    "intake": "profile_info",
    "matched": "ask_odds",
    "odds": "set_list",
    "listing": "ask_plan",
    "plan": "help",
}


def classify(text: str, stage: Stage) -> Intent:
    # Walks the golden path: each stage expects the next demo turn.
    return _NEXT_INTENT[stage]


def match(profile: FamilyProfile) -> list[SchoolMatch]:
    return [SchoolMatch.model_validate(m) for m in _load("maria_matches.json")]


def estimate(profile: FamilyProfile, codes: list[str]) -> list[PointsEstimate]:
    return [PointsEstimate.model_validate(e) for e in _load("maria_estimates.json")]


def check_list(profile: FamilyProfile, codes: list[str]) -> list[GuardrailWarning]:
    return [GuardrailWarning.model_validate(w) for w in _load("maria_warnings.json")]


def build_plan(profile: FamilyProfile, codes: list[str], lang: Language, today: date) -> ActionPlan:
    return ActionPlan.model_validate(_load("maria_plan.json"))


def render_matches(matches: list[SchoolMatch], profile: FamilyProfile) -> str:
    return "\n".join(f"{i}. {m.name} ({m.theme})" for i, m in enumerate(matches, 1))


def render_points(estimates: list[PointsEstimate], profile: FamilyProfile) -> str:
    return "\n".join(f"{e.code}: {e.total}/{e.max_possible}" for e in estimates)


def render_warnings(warnings: list[GuardrailWarning], profile: FamilyProfile) -> str:
    return "\n".join(f"[{w.severity}] {w.code}" for w in warnings)


def render_plan(plan: ActionPlan, profile: FamilyProfile) -> str:
    return "\n".join([*plan.checklist, *plan.steps, plan.email_subject])
