"""School context from CDE (enrollment, English learners, students with IEPs). Owner: Dev B, ticket B14 (#48).

Reads data/school_facts.json (built offline by scripts/build_school_facts.py). These describe the whole host
school, not only the magnet, and are context, never a ranking or a quality judgment: callers show plain
fractions ("about 1 in 6"), never percentages. Missing or suppressed numbers stay None. FACTS=off disables.
"""
from __future__ import annotations

import os

from core.config import load_config, load_data


def enabled() -> bool:
    return os.getenv("FACTS", "on").strip().lower() not in ("off", "0", "false", "no")


def fraction(count: int | None, total: int | None) -> tuple[int, int] | None:
    """Plain fraction for count/total: (1, 6) = 'about 1 in 6'; (4, 10) = 'about 4 in 10' for big shares."""
    if not count or not total or count > total:
        return None
    share = count / total
    if share >= load_config("school_facts")["fraction_tenths_at"]:
        return round(share * 10), 10
    return 1, max(2, round(1 / share))


def facts(code: str) -> dict:
    """Facts for a Choices program code (via its host school's CDS code); {} when unknown or FACTS=off.

    {"enrollment": 840, "year": "2025-26", "english_learners": (1, 9), "iep": (1, 6),
     "chronic_absenteeism_pct": 20.7, "chronic_absenteeism_year": "2024-25", "cds_code": "...", "source_url": ...}
    """
    if not enabled():
        return {}
    school = next((s for s in load_data("schools") if s["code"] == code), None)
    data = load_data("school_facts")
    row = data["schools"].get(school.get("cds_code") or "") if school else None
    if not row:
        return {}
    total = row["enrollment"]
    return {
        "cds_code": school["cds_code"],
        "year": row["year"],
        "enrollment": round(total, -1) if total else None,  # "about 840 students"
        "english_learners": fraction(row["english_learners"], total),
        "iep": fraction(row["students_with_disabilities"], total),
        "chronic_absenteeism_pct": row["chronic_absenteeism_pct"],
        "chronic_absenteeism_year": row["chronic_absenteeism_year"],
        "source_url": data["sources"]["enrollment"]["url"],
    }
