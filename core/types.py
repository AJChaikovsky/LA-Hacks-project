"""Shared core types. FROZEN at G0: add fields freely, never rename or remove one.

Reasons and warnings travel as codes, not sentences; core/render.py localizes them.
"""
from typing import Literal

from pydantic import BaseModel


class FamilyProfile(BaseModel):
    language: Literal["es", "en"] = "en"
    child_name: str | None = None
    current_grade: int | None = None          # 5
    applying_grade: int | None = None         # 6 (derived: current + 1)
    interests: list[str] = []                 # normalized tags: arts, stem, medicine, law, media, languages, sports
    iep: bool | None = None
    iep_notes: str | None = None
    english_learner: bool | None = None
    home_language: str | None = None          # "Spanish"
    home_neighborhood: str | None = None      # "Boyle Heights"
    resident_school: str | None = None
    resident_phbao: bool | None = None        # None = unknown -> ask or caveat
    resident_overcrowded: bool | None = None
    in_magnet_now: bool = False
    matriculating: bool = False               # finishing the top grade of a magnet, applying to the next level
    waitlist_years: int = 0
    sibling_program_codes: list[str] = []
    parent_work_area: str | None = None       # "Downtown"
    device: str = "phone"
    intended_choices: list[str] = []          # program codes in rank order
    would_not_accept: list[str] = []
    gifted: bool | None = None                # mentions gifted / GATE identification
    late_application: bool = False


class SchoolMatch(BaseModel):
    code: str
    name: str
    program_type: str                         # magnet | dle | charter
    theme: str
    score: float
    reason_codes: list[str]                   # e.g. ["arts_in_steam", "near_home", "spanish_dle"]
    distance_km: float | None


class PointsLine(BaseModel):
    factor: str                               # matriculation | waitlist | phbao | overcrowded | sibling | late
    points: int
    status: Literal["confirmed", "assumed", "unknown"]


class PointsEstimate(BaseModel):
    code: str
    applicable: bool                          # False for DLE / charter / EMC
    total: int
    max_possible: int = 23
    lines: list[PointsLine]
    competitiveness: Literal["very_high", "high", "moderate", "unknown"]
    apps_per_opening: float | None            # from the official page, else None


class Warning(BaseModel):
    code: str                                 # see WARNING_CODES
    severity: Literal["block", "warn", "info"]
    params: dict = {}


class ActionPlan(BaseModel):
    checklist: list[str]
    deadlines: list[dict]                     # {"key", "date", "days_left", "label_es", "label_en"}
    steps: list[str]
    email_subject: str
    email_body: str
    warnings: list[Warning]


# Warning codes core/guardrails.py may emit (B5) and core/render.py must localize (C3).
WARNING_CODES: dict[str, str] = {
    "over_limit_magnet": "block",
    "would_not_accept": "warn",
    "decline_wipes_points": "info",
    "first_choice_waitlist": "info",
    "gifted_verification": "warn",
    "far_commute": "warn",
    "current_magnet_drop": "warn",
    "past_deadline": "warn",
    "not_points_program": "info",
}

Stage = Literal["intake", "matched", "odds", "listing", "plan"]
STAGES: tuple[str, ...] = ("intake", "matched", "odds", "listing", "plan")
