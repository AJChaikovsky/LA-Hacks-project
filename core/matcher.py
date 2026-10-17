"""Filter and rank programs from data/schools.json for a FamilyProfile; returns the top 3 SchoolMatch.

Owner: Dev B, ticket B3 (#12). Must match the signature in core/stubs.py where one is listed.

Score = theme fit + distance from home + language fit + commute to the parent's work, weighted by
config/matcher.json. The best-scoring programs fill the list, except one slot is kept for the program
closest to home when the formula leaves it out. Reasons travel as codes; core/render.py localizes them.
"""
from __future__ import annotations

import math

from core.config import load_config, load_data
from core.types import FamilyProfile, ReasonCode, School, SchoolMatch


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def schools() -> list[School]:
    return [School.model_validate(r) for r in load_data("schools")]


def place_coords(name: str | None) -> tuple[float, float] | None:
    """Centroid for a neighborhood name or alias from data/places.json, else None."""
    if not name:
        return None
    key = name.strip().lower()
    for canonical, p in load_data("places")["neighborhoods"].items():
        if key == canonical.lower() or key in p["aliases"]:
            return p["lat"], p["lng"]
    return None


def outside_lausd(name: str | None) -> str | None:
    """The other school district's name when the neighborhood is a city outside LAUSD, else None."""
    if not name:
        return None
    key = name.strip().lower()
    for canonical, p in load_data("places")["neighborhoods"].items():
        if (key == canonical.lower() or key in p["aliases"]) and p.get("outside_lausd"):
            return p["district"]
    return None


def distance_km(place: tuple[float, float] | None, school: School) -> float | None:
    if place is None or school.lat is None or school.lng is None:
        return None
    return haversine_km(place[0], place[1], school.lat, school.lng)


def applying_grade(profile: FamilyProfile) -> int | None:
    if profile.applying_grade is not None:
        return profile.applying_grade
    return profile.current_grade + 1 if profile.current_grade is not None else None


def eligible(profile: FamilyProfile, school: School) -> bool:
    """Hard filters: the applying grade is served, and Gifted programs only for identified gifted kids."""
    grade = applying_grade(profile)
    if grade is not None and grade not in school.grades:
        return False
    return not school.gifted_verification or profile.gifted is True


def theme_fit(profile: FamilyProfile, school: School) -> float:
    cfg = load_config("matcher")
    if not profile.interests:
        return cfg["no_interest_theme_score"]
    return max(cfg["theme_map"].get(i, {}).get(school.theme, 0.0) for i in profile.interests)


def _closeness(km: float | None) -> float:
    cfg = load_config("matcher")
    if km is None:
        return cfg["unknown_place_score"]
    return max(0.0, 1.0 - km / cfg["distance_scale_km"])


def language_fit(profile: FamilyProfile, school: School) -> float:
    home = (profile.home_language or "").lower()
    return 1.0 if home and home in (lang.lower() for lang in school.languages) else 0.0


def score(profile: FamilyProfile, school: School) -> float:
    w = load_config("matcher")["weights"]
    home_km = distance_km(place_coords(profile.home_neighborhood), school)
    work_km = distance_km(place_coords(profile.parent_work_area), school)
    return round(w["theme"] * theme_fit(profile, school) + w["distance"] * _closeness(home_km)
                 + w["language"] * language_fit(profile, school) + w["commute"] * _closeness(work_km), 4)


def reason_codes(profile: FamilyProfile, school: School) -> list[ReasonCode]:
    cfg = load_config("matcher")
    home_km = distance_km(place_coords(profile.home_neighborhood), school)
    work_km = distance_km(place_coords(profile.parent_work_area), school)
    reasons: list[ReasonCode] = []
    exact = [i for i in profile.interests if cfg["theme_map"].get(i, {}).get(school.theme) == 1.0]
    if exact:
        reasons.append("theme_match")
    elif "arts" in profile.interests and school.theme == "STEAM":
        reasons.append("arts_in_steam")
    elif "arts" in profile.interests and school.theme == "New Media":
        reasons.append("arts_in_media")
    if home_km is not None and home_km <= cfg["near_home_km"]:
        reasons.append("near_home")
    if work_km is not None and work_km <= cfg["near_work_km"]:
        reasons.append("near_work")
    if language_fit(profile, school) and school.program_type == "dle":
        reasons.append("spanish_dle")
    if home_km is not None and home_km > cfg["bus_min_km"] and school.program_type == "magnet":
        reasons.append("bus_possible")
    if profile.iep and school.program_type == "magnet":
        reasons.append("iep_eligible")
    return reasons


def _vapa_nearby(profile: FamilyProfile) -> bool:
    home = place_coords(profile.home_neighborhood)
    limit = load_config("matcher")["vapa_nearby_km"]
    return any(s.theme == "VAPA" and eligible(profile, s) and (d := distance_km(home, s)) is not None and d <= limit
               for s in schools())


def _to_match(profile: FamilyProfile, school: School) -> SchoolMatch:
    km = distance_km(place_coords(profile.home_neighborhood), school)
    return SchoolMatch(code=school.code, name=school.name, program_type=school.program_type, theme=school.theme,
                       score=score(profile, school), reason_codes=reason_codes(profile, school),
                       distance_km=round(km, 1) if km is not None else None,
                       address=school.address, phone=school.phone)


def match(profile: FamilyProfile) -> list[SchoolMatch]:
    cfg = load_config("matcher")
    ranked = sorted((_to_match(profile, s) for s in schools() if eligible(profile, s)),
                    key=lambda m: (-m.score, m.code))
    top = ranked[: cfg["top_n"]]

    slot = cfg["near_home_slot"]
    nearby = [m for m in ranked if m.distance_km is not None and m.distance_km <= slot["max_km"]]
    if slot["enabled"] and nearby and len(top) == cfg["top_n"]:
        closest = min(nearby, key=lambda m: (m.distance_km, -m.score, m.code))
        if closest not in top:
            top = top[: cfg["top_n"] - 1]
            top.insert(min(slot["position"], len(top) + 1) - 1, closest)

    if top and "arts" in profile.interests and not _vapa_nearby(profile):
        top[0].reason_codes.insert(0, "no_vapa_nearby")
    return top
