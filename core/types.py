"""Shared data contracts for every module and agent.

FROZEN at gate G0. Rules for changing this file:
- Adding a field (with a default) or a new code to a Literal list: OK, say so in team chat.
- Renaming or removing anything: needs all three devs to agree first.

Reasons, warnings, checklist items, steps and deadlines travel as *codes*, never as
sentences. core/render.py turns codes into Spanish or English, so the core logic stays
language-free and the LLM can't invent facts.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Frozen code lists
# ---------------------------------------------------------------------------

Language = Literal["es", "en"]
ProgramType = Literal["magnet", "dle", "charter"]
Stage = Literal["intake", "matched", "odds", "listing", "plan"]
Intent = Literal[
    "profile_info",    # parent describes the child or adds facts
    "ask_odds",        # "¿qué posibilidades tiene?"
    "set_list",        # "voy a poner las tres..."
    "ask_plan",        # "¿qué necesito para terminar?"
    "card_selection",  # a click on an Interactive Card
    "reset",
    "help",
    "off_topic",
]
Interest = Literal[
    "arts", "stem", "medicine", "law", "media", "languages", "sports", "business", "environment",
]
ReasonCode = Literal[
    "theme_match",      # program theme matches an interest exactly (params: interest)
    "arts_in_steam",    # STEAM program includes arts
    "arts_in_media",    # New Media program is arts-adjacent
    "no_vapa_nearby",   # no VAPA middle-school magnet within range; explains the trade-off
    "near_home",
    "near_work",
    "spanish_dle",      # Spanish dual-language program fits the home language
    "iep_eligible",     # students with IEPs can apply to any magnet
    "bus_possible",     # may qualify for district transportation
]
WarningCode = Literal[
    "over_limit_magnet",     # block: more than 3 magnet choices
    "would_not_accept",      # warn: listed a school the parent doubts they'd accept
    "decline_wipes_points",  # info: declining an offer erases all waiting-list points
    "first_choice_waitlist", # info: if not selected, waitlisted at 1st choice only
    "gifted_verification",   # warn: gifted magnet needs eligibility verification
    "far_commute",           # warn: long trip and no confirmed bus
    "current_magnet_drop",   # warn: selected into another magnet = lose current seat
    "past_deadline",         # warn: after Nov 6, late application, no points
    "not_points_program",    # info: DLE / charter use lotteries, not points
]
Severity = Literal["block", "warn", "info"]
PointsFactor = Literal["matriculation", "waitlist", "phbao", "overcrowded", "sibling"]
PointsStatus = Literal["confirmed", "assumed", "unknown"]
Competitiveness = Literal["very_high", "high", "moderate", "unknown"]
ChecklistCode = Literal[
    "parent_portal_account",
    "student_id",
    "address_on_file",
    "sibling_info",
    "gifted_form",
    "iep_copy",
]
StepCode = Literal[
    "open_apply_site",
    "sign_in_or_register",
    "add_student",
    "choose_program_type",
    "rank_magnet_choices",
    "add_dle_choice",
    "list_siblings",
    "check_address",
    "submit_and_save_confirmation",
]
DeadlineKey = Literal[
    "on_time_open",
    "on_time_deadline",
    "late_window_opens",
    "confirmation_notice",
    "correction_deadline",
    "results",
    "accept_decline",
    "school_starts",
]

# Tuples for iteration, e.g. building i18n files or validating config.
LANGUAGES = get_args(Language)
STAGES = get_args(Stage)
INTENTS = get_args(Intent)
INTERESTS = get_args(Interest)
REASON_CODES = get_args(ReasonCode)
WARNING_CODES = get_args(WarningCode)
CHECKLIST_CODES = get_args(ChecklistCode)
STEP_CODES = get_args(StepCode)
DEADLINE_KEYS = get_args(DeadlineKey)

MAX_MAGNET_POINTS = 23

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class School(BaseModel):
    """One Choices program as stored in data/schools.json (owned by Dev B, ticket B1)."""

    code: str                        # Choices program code, e.g. "1804703" or "1838701TWS"
    name: str
    program_type: ProgramType
    theme: str                       # brochure theme, e.g. "STEAM", "VAPA", "Gifted"
    sub_theme: str | None = None
    grades: list[int]
    address: str
    zip: str
    lat: float | None = None
    lng: float | None = None
    region: str | None = None        # brochure region letter, e.g. "E"
    phone: str | None = None
    website: str | None = None
    choices_url: str | None = None
    gifted_verification: bool = False
    languages: list[str] = Field(default_factory=list)
    transport_note: str | None = None
    capacity_2026_27: int | None = None
    applications_2025_26: int | None = None
    openings_2026_27: int | None = None
    verified: bool = False
    source_url: str


class FamilyProfile(BaseModel):
    language: Language = "en"
    child_name: str | None = None
    current_grade: int | None = None
    applying_grade: int | None = None          # usually current_grade + 1
    interests: list[Interest] = Field(default_factory=list)
    iep: bool | None = None
    iep_notes: str | None = None
    english_learner: bool | None = None
    home_language: str | None = None
    home_neighborhood: str | None = None
    resident_school: str | None = None
    resident_phbao: bool | None = None         # None = unknown -> caveat or ask
    resident_overcrowded: bool | None = None
    in_magnet_now: bool = False
    waitlist_years: int = 0
    sibling_program_codes: list[str] = Field(default_factory=list)
    parent_work_area: str | None = None
    device: str = "phone"
    intended_choices: list[str] = Field(default_factory=list)  # program codes, rank order
    would_not_accept: list[str] = Field(default_factory=list)  # program codes


class SchoolMatch(BaseModel):
    code: str
    name: str
    program_type: ProgramType
    theme: str
    score: float
    reason_codes: list[ReasonCode]
    distance_km: float | None = None
    address: str | None = None
    phone: str | None = None


class PointsLine(BaseModel):
    factor: PointsFactor
    points: int
    status: PointsStatus


class PointsEstimate(BaseModel):
    code: str
    applicable: bool                           # False for DLE / charter
    total: int = 0
    max_possible: int = MAX_MAGNET_POINTS
    lines: list[PointsLine] = Field(default_factory=list)
    competitiveness: Competitiveness = "unknown"
    apps_per_opening: float | None = None      # from the official Choices page, else None


class GuardrailWarning(BaseModel):
    code: WarningCode
    severity: Severity
    params: dict = Field(default_factory=dict)  # e.g. {"codes": ["1823703"]}


class Deadline(BaseModel):
    key: DeadlineKey
    date: date | None                          # None when only the month is known
    days_left: int | None = None


class ActionPlan(BaseModel):
    checklist: list[ChecklistCode]
    deadlines: list[Deadline]
    steps: list[StepCode]
    email_subject: str                         # already in the parent's language
    email_body: str
    warnings: list[GuardrailWarning] = Field(default_factory=list)


class SessionState(BaseModel):
    """Per-parent conversation state, stored by the orchestrator in ctx.storage."""

    stage: Stage = "intake"
    profile: FamilyProfile = Field(default_factory=FamilyProfile)
    matches: list[SchoolMatch] = Field(default_factory=list)
    estimates: list[PointsEstimate] = Field(default_factory=list)
    warnings: list[GuardrailWarning] = Field(default_factory=list)
    plan: ActionPlan | None = None
