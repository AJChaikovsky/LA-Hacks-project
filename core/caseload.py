"""Caseload report for partner agents (B12). Owner: Dev B, ticket B12 (#39).

Built only from core/ (match, estimate, check_list); same rules as the parent chat: no percentages,
"probably" when a point line is assumed. Families always get this free through choices-counselor;
partner agents (nonprofits, parent centers, counselors) pay per family for a batch report.
"""
from __future__ import annotations

from decimal import Decimal

from core.config import load_config
from core.guardrails import check_list
from core.matcher import match
from core.points import estimate
from core.types import FamilyProfile

_SEVERITY_RANK = {"block": 0, "warn": 1, "info": 2}
# Within a severity, the facts that change the whole application come first.
_PRIORITY = ["over_limit_magnet", "outside_district", "past_deadline", "current_magnet_drop", "would_not_accept",
             "gifted_verification", "far_commute"]


def price(n_families: int) -> tuple[str, int]:
    """(amount in FET as a string, amount in atestfet) for a batch of n families."""
    cfg = load_config("caseload")
    fet = Decimal(cfg["price_per_family_fet"]) * n_families
    return format(fet.normalize(), "f"), int(fet * cfg["atto_per_fet"])


def _points_summary(estimates) -> dict | None:
    magnets = [e for e in estimates if e.applicable]
    if not magnets:
        return None
    first = magnets[0]
    assumed = any(line.status == "assumed" and line.points for line in first.lines)
    unknown = [line.factor for line in first.lines if line.status == "unknown"]
    return {"total": first.total, "max": first.max_possible, "probably": assumed, "unknown_factors": unknown}


def family_report(profile: FamilyProfile) -> dict:
    matches = match(profile)
    codes = [m.code for m in matches]
    warnings = sorted(check_list(profile, codes), key=lambda w: (
        _SEVERITY_RANK[w.severity], _PRIORITY.index(w.code) if w.code in _PRIORITY else len(_PRIORITY)))
    return {
        "child": profile.child_name,
        "language": profile.language,
        "matches": [{"code": m.code, "name": m.name, "theme": m.theme, "distance_km": m.distance_km} for m in matches],
        "points": _points_summary(estimate(profile, codes)),
        "top_warning": {"code": warnings[0].code, "severity": warnings[0].severity} if warnings else None,
    }


def build_report(profiles: list[FamilyProfile]) -> dict:
    return {"families": [family_report(p) for p in profiles],
            "disclaimer": "Estimates only. Confirm at choices.lausd.net or (213) 241-4177."}


def payment_matches(event_maps: list[dict], recipient: str, amount_atto: int, denom: str) -> bool:
    """True when a confirmed tx's transfer events paid at least amount_atto of denom to recipient.

    `event_maps` are cosmpy event dicts like {"transfer": {"recipient": ..., "amount": "300...atestfet"}}:
    TxResponse.events plus each TxResponse.logs[i].events (newer chains leave logs empty).
    """
    for events in event_maps:
        transfer = events.get("transfer", {})
        if transfer.get("recipient") != recipient:
            continue
        for part in transfer.get("amount", "").split(","):
            if part.endswith(denom) and part[: -len(denom)].isdigit() and int(part[: -len(denom)]) >= amount_atto:
                return True
    return False
