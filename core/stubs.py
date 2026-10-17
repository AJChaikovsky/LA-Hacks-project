"""Canned Maria data so Dev A can wire the agent before B and C land their modules.

Each function mirrors the planned signature of the real one. Replace the import in the
orchestrator with the real module as each card merges.
"""
from datetime import date

from core.types import ActionPlan, FamilyProfile, PointsEstimate, PointsLine, SchoolMatch, Warning

MARIA = FamilyProfile(
    language="es",
    child_name="Diego",
    current_grade=5,
    applying_grade=6,
    interests=["arts"],
    iep=True,
    home_language="Spanish",
    home_neighborhood="Boyle Heights",
    parent_work_area="Downtown",
)

MARIA_MATCHES = [
    SchoolMatch(code="1804703", name="Bel Vedere MS", program_type="magnet", theme="STEAM",
                score=0.82, reason_codes=["arts_in_steam", "no_vapa_nearby"], distance_km=4.1),
    SchoolMatch(code="1817902", name="Hollenbeck MS", program_type="magnet", theme="STEM/Medicine",
                score=0.74, reason_codes=["near_home"], distance_km=1.2),
    SchoolMatch(code="1804702", name="Bel Vedere MS", program_type="magnet", theme="New Media",
                score=0.71, reason_codes=["arts_adjacent_media"], distance_km=4.1),
]


def extract(text: str, profile: FamilyProfile | None = None) -> FamilyProfile:
    return MARIA


def classify(text: str, stage: str) -> str:
    return {"intake": "recommend", "matched": "odds", "odds": "set_list", "listing": "plan"}.get(stage, "plan")


def match(profile: FamilyProfile) -> list[SchoolMatch]:
    return MARIA_MATCHES


def estimate(profile: FamilyProfile, program: dict) -> PointsEstimate:
    if program.get("program_type", "magnet") != "magnet":
        return PointsEstimate(code=program["code"], applicable=False, total=0, lines=[],
                              competitiveness="unknown", apps_per_opening=None)
    return PointsEstimate(
        code=program["code"],
        applicable=True,
        total=4,
        lines=[PointsLine(factor="phbao", points=4, status="assumed")],
        competitiveness="unknown",
        apps_per_opening=None,
    )


def check_list(profile: FamilyProfile, codes: list[str]) -> list[Warning]:
    warnings = [Warning(code="decline_wipes_points", severity="info"),
                Warning(code="first_choice_waitlist", severity="info")]
    if len(codes) > 3:
        warnings.insert(0, Warning(code="over_limit_magnet", severity="block", params={"extra": codes[3:]}))
    if "1823703" in codes:
        warnings.insert(1, Warning(code="would_not_accept", severity="warn", params={"code": "1823703"}))
    return warnings


def suggest_alternatives(profile: FamilyProfile, codes: list[str]) -> list[str]:
    return ["1838701TWS"] if len(codes) > 3 else []


def checklist(profile: FamilyProfile) -> list[str]:
    return ["parent_portal_account", "student_id", "home_address_on_file", "iep_copy_reference"]


def deadlines(today: date) -> list[dict]:
    on_time = date(2026, 11, 6)
    return [{"key": "on_time_deadline", "date": on_time.isoformat(), "days_left": (on_time - today).days,
             "label_es": "Fecha límite a tiempo", "label_en": "On-time deadline"}]


def phone_steps(lang: str) -> list[str]:
    return ["sign_in", "add_student", "choose_program", "rank_magnets", "list_siblings", "check_address", "submit",
            "keep_confirmation"]


def plan(profile: FamilyProfile, codes: list[str], lang: str, today: date) -> ActionPlan:
    return ActionPlan(
        checklist=checklist(profile),
        deadlines=deadlines(today),
        steps=phone_steps(lang),
        email_subject="Preguntas sobre el programa magnet",
        email_body="(stub) Borrador del correo al coordinador.",
        warnings=check_list(profile, codes),
    )


def render(intent: str, payload: dict, lang: str) -> str:
    return f"(stub {lang}) {intent}"
