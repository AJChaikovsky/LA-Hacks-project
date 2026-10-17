"""List checks: 3-magnet limit, would-not-accept, decline wipes points, first-choice waitlist, etc.

Owner: Dev B, ticket B5 (#14). Must match the signature in core/stubs.py where one is listed.

Warnings travel as codes with params ({"codes": [...]}, {"first_choice": code}, {"suggest": code});
core/render.py turns them into sentences.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.config import load_config
from core.matcher import distance_km, eligible, language_fit, place_coords, schools
from core.types import WARNING_CODES, FamilyProfile, GuardrailWarning, School, WarningCode

_SEVERITY = {
    "over_limit_magnet": "block",
    "would_not_accept": "warn",
    "gifted_verification": "warn",
    "far_commute": "warn",
    "current_magnet_drop": "warn",
    "past_deadline": "warn",
    "decline_wipes_points": "info",
    "first_choice_waitlist": "info",
    "not_points_program": "info",
}
assert set(_SEVERITY) == set(WARNING_CODES)

FAR_KM = 15.0
MILE_KM = 1.609344


def _warn(code: WarningCode, **params) -> GuardrailWarning:
    return GuardrailWarning(code=code, severity=_SEVERITY[code], params=params)


def today_la() -> date:
    return datetime.now(ZoneInfo(load_config("deadlines")["timezone"])).date()


def _on_time_deadline() -> date:
    row = next(d for d in load_config("deadlines")["deadlines"] if d["key"] == "on_time_deadline")
    return date.fromisoformat(row["date"])


def suggest_alternatives(profile: FamilyProfile, codes: list[str]) -> list[str]:
    """Non-magnet programs (DLE, charter) not already listed; they don't use a magnet slot.

    Best first: home-language fit, then closest to home, then code.
    """
    home = place_coords(profile.home_neighborhood)
    options = [s for s in schools() if s.program_type != "magnet" and s.code not in codes and eligible(profile, s)]

    def key(s: School):
        km = distance_km(home, s)
        return (-language_fit(profile, s), km if km is not None else float("inf"), s.code)

    return [s.code for s in sorted(options, key=key)]


def check_list(profile: FamilyProfile, codes: list[str], today: date | None = None) -> list[GuardrailWarning]:
    """Warnings for the parent's ranked list of program codes (codes[0] is the 1st choice)."""
    if not codes:
        return []
    by_code = {s.code: s for s in schools()}
    listed = [by_code[c] for c in codes if c in by_code]
    magnets = [s.code for s in listed if s.program_type == "magnet"]
    limit = load_config("limits")["magnet_max_choices"]
    bus_km = load_config("limits")["bus_radius_miles_secondary"] * MILE_KM
    home = place_coords(profile.home_neighborhood)
    today = today or today_la()
    out: list[GuardrailWarning] = []

    extra = magnets[limit:]
    if extra:
        out.append(_warn("over_limit_magnet", codes=extra, limit=limit))

    doubted = [c for c in codes if c in profile.would_not_accept]
    if doubted:
        out.append(_warn("would_not_accept", codes=doubted))

    gifted = [s.code for s in listed if s.gifted_verification]
    if gifted:
        out.append(_warn("gifted_verification", codes=gifted))

    # Over 2 miles needs a bus route we can't confirm; over 15 km is far either way.
    far = [s.code for s in listed if (km := distance_km(home, s)) is not None and km > bus_km
           and (km > FAR_KM or not s.transport_note)]
    if far:
        out.append(_warn("far_commute", codes=far))

    if profile.in_magnet_now and magnets:
        out.append(_warn("current_magnet_drop", codes=magnets))

    if profile.late_application or today > _on_time_deadline():
        out.append(_warn("past_deadline", deadline=_on_time_deadline().isoformat()))

    if magnets:
        out.append(_warn("decline_wipes_points"))
        out.append(_warn("first_choice_waitlist", first_choice=magnets[0]))

    non_points = [s.code for s in listed if s.program_type != "magnet"]
    if non_points:
        out.append(_warn("not_points_program", codes=non_points, suggest=non_points[0]))
    elif extra:
        alternatives = suggest_alternatives(profile, codes)
        if alternatives:
            out.append(_warn("not_points_program", suggest=alternatives[0]))
    return out
