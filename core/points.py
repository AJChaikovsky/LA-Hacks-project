"""Magnet Priority Points estimate from config/points.json; magnets only.

Owner: Dev B, ticket B4 (#13). Must match the signature in core/stubs.py where one is listed.

Every line carries a status so the renderer can be honest:
- confirmed: the parent told us, or it follows from an official list for a school the parent named
- assumed:   inferred, e.g. the resident school guessed from the neighborhood ("probablemente")
- unknown:   we can't tell; counts 0 and the renderer asks the parent
"""
from __future__ import annotations

import re

from core.config import load_config, load_data
from core.matcher import outside_lausd
from core.types import FamilyProfile, PointsEstimate, PointsLine, PointsStatus, School

_SUFFIXES = [(r"\bmiddle school\b", "ms"), (r"\bmiddle\b", "ms"), (r"\belementary( school)?\b", "el"),
             (r"\bsenior high( school)?\b", "sh"), (r"\bhigh school\b", "sh"), (r"\bstreet\b", "st"),
             (r"\bavenue\b", "ave")]


def _norm(name: str) -> str:
    n = name.lower().strip()
    for pattern, short in _SUFFIXES:
        n = re.sub(pattern, short, n)
    return re.sub(r"[^a-z0-9 ]", "", n).strip()


def lookup_resident_school(name: str | None) -> dict | None:
    """Row from the official Norm Category/Overcrowded list for a school name, else None."""
    if not name:
        return None
    rows = load_data("resident_schools")["schools"]
    key = _norm(name)
    exact = [r for r in rows if _norm(r["name"]) == key]
    if exact:
        return exact[0]
    loose = [r for r in rows if _norm(r["name"]).startswith(key + " ")]
    return loose[0] if len(loose) == 1 else None


def _neighborhood_resident_school(neighborhood: str | None) -> str | None:
    """Resident middle school recorded for a landmark in that neighborhood (data/places.json)."""
    if not neighborhood:
        return None
    for landmark in load_data("places")["landmarks"].values():
        if landmark.get("neighborhood", "").lower() == neighborhood.strip().lower():
            return landmark.get("resident_middle_school")
    return None


def resident_flags(profile: FamilyProfile) -> dict[str, tuple[bool | None, PointsStatus]]:
    """{"phbao": (value, status), "overcrowded": (value, status)} for the child's resident school."""
    row, status = lookup_resident_school(profile.resident_school), "confirmed"
    if row is None and profile.resident_school is None:
        row, status = lookup_resident_school(_neighborhood_resident_school(profile.home_neighborhood)), "assumed"
    flags = {}
    for factor, stated in (("phbao", profile.resident_phbao), ("overcrowded", profile.resident_overcrowded)):
        if stated is not None:
            flags[factor] = (stated, "confirmed")
        elif row is not None:
            flags[factor] = (row[factor], status)
        else:
            flags[factor] = (None, "unknown")
    return flags


def competitiveness(school: School) -> tuple[str, float | None]:
    apps, openings = school.applications_2025_26, school.openings_2026_27
    if apps is None or openings is None:
        return "unknown", None
    if openings == 0:
        return "very_high", None
    ratio = round(apps / openings, 1)
    band = next(b["band"] for b in load_config("competitiveness")["bands"] if ratio >= b["min_apps_per_opening"])
    return band, ratio


def _estimate_one(profile: FamilyProfile, school: School, first_choice: bool) -> PointsEstimate:
    if school.program_type != "magnet":
        return PointsEstimate(code=school.code, applicable=False)
    f = load_config("points")["factors"]
    band, ratio = competitiveness(school)
    lines: list[PointsLine] = []

    for factor, (value, status) in resident_flags(profile).items():
        lines.append(PointsLine(factor=factor, points=f[factor]["points"] if value else 0, status=status))

    # Matriculation and waiting-list points are never combined.
    waitlist = 0 if profile.matriculating else f["waitlist"]["points"] * min(profile.waitlist_years,
                                                                              f["waitlist"]["max_years"])
    lines.append(PointsLine(factor="waitlist", points=waitlist, status="confirmed"))
    sibling = first_choice and school.code in profile.sibling_program_codes
    lines.append(PointsLine(factor="sibling", points=f["sibling"]["points"] if sibling else 0, status="confirmed"))
    lines.append(PointsLine(factor="matriculation", points=f["matriculation"]["points"] if profile.matriculating else 0,
                            status="confirmed"))

    if profile.late_application or outside_lausd(profile.home_neighborhood):  # late or out-of-district: no points
        lines = [line.model_copy(update={"points": 0, "status": "confirmed"}) for line in lines]
    total = min(sum(line.points for line in lines if line.status != "unknown"), load_config("points")["max_total"])
    return PointsEstimate(code=school.code, applicable=True, total=total, lines=lines,
                          competitiveness=band, apps_per_opening=ratio)


def estimate(profile: FamilyProfile, codes: list[str]) -> list[PointsEstimate]:
    """One estimate per known program code, in the order given (codes[0] is the 1st choice)."""
    by_code = {s["code"]: School.model_validate(s) for s in load_data("schools")}
    return [_estimate_one(profile, by_code[c], first_choice=(i == 0))
            for i, c in enumerate(codes) if c in by_code]
