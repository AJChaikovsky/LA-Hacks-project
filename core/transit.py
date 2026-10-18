"""Direct LA Metro bus lines from home to a school, from the prebuilt index. Owner: Dev B, ticket B13 (#47).

Reads data/transit_index.json (built offline by scripts/build_transit.py from the official Metro bus GTFS).
No network at runtime. Times are the timetable's median weekday-morning ride, so callers must say
"about" and never "will take". Unknown places return [] (no guess). TRANSIT=off turns it all off.
"""
from __future__ import annotations

import os

from core.config import load_config, load_data
from core.types import FamilyProfile, TransitOption


def enabled() -> bool:
    return os.getenv("TRANSIT", "on").strip().lower() not in ("off", "0", "false", "no")


def canonical_place(name: str | None) -> str | None:
    """'boyle hts' -> 'Boyle Heights' (data/places.json neighborhoods and aliases), else None."""
    if not name:
        return None
    key = name.strip().lower()
    for canonical, p in load_data("places")["neighborhoods"].items():
        if key == canonical.lower() or key in p["aliases"]:
            return canonical
    return None


def _round(minutes: int) -> int:
    step = load_config("transit")["round_minutes_to"]
    return max(step, step * round(minutes / step))


def transit_options(profile: FamilyProfile, code: str) -> list[TransitOption]:
    """Direct Metro lines from the family's neighborhood to program `code`, quickest first; [] when unknown."""
    if not enabled():
        return []
    place = canonical_place(profile.home_neighborhood)
    if place is None:
        return []
    rows = load_data("transit_index")["places"].get(place, {}).get(code, [])
    return [TransitOption(route=r["route"], minutes=_round(r["minutes"]), walk_m=r["walk_m"],
                          alight_stop=r.get("alight_stop")) for r in rows]
