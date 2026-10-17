"""ASI:One Interactive Card builders: matches carousel, school detail, packet review.

Owner: Dev C, ticket C4 (#22).

    matches_carousel(matches, profile, estimates=None) -> dict      card_kind "carousel"
    school_detail(match, profile, estimate=None) -> dict            card_kind "detail"
    packet_review(plan, profile, codes) -> dict                     card_kind "review"
    card_message(narration, kind, payload) -> ChatMessage           text + MetadataContent
    respond_to_selection(selection, state) -> CardReply             what to send after a click
    validate(kind, payload) -> list[str]                            [] when ASI:One should accept it

ASI:One silently drops an invalid card and shows only the text, so every payload goes
through validate() in tests, and the narration text is always a complete reply on its own.
Set CARDS=off to send text only.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

from core.render import format_date, program_name, schools, strings
from core.types import ActionPlan, FamilyProfile, PointsEstimate, SchoolMatch, SessionState

MAX_PAYLOAD_BYTES = 64 * 1024
KINDS = ("carousel", "detail", "form", "review", "custom")


def cards_enabled() -> bool:
    return os.getenv("CARDS", "on").lower() != "off"


def _c(profile: FamilyProfile) -> dict:
    return strings(profile.language)["cards"]


def _child(profile: FamilyProfile) -> str:
    return profile.child_name or strings(profile.language)["child_default"]


def _points_text(estimate: PointsEstimate | None, profile: FamilyProfile) -> str | None:
    if estimate is None:
        return None
    c = _c(profile)
    if not estimate.applicable:
        return c["no_points"]
    total = sum(line.points for line in estimate.lines if line.status != "unknown")
    assumed = any(line.status == "assumed" and line.points for line in estimate.lines)
    return (c["points_maybe"] if assumed else c["points_value"]).format(total=total)


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def matches_carousel(matches: list[SchoolMatch], profile: FamilyProfile,
                     estimates: list[PointsEstimate] | None = None) -> dict:
    c, lang = _c(profile), profile.language
    by_code = {e.code: e for e in estimates or []}
    items = []
    for m in matches:
        bits = [m.theme]
        if m.distance_km is not None:
            bits.append(f"{m.distance_km:.1f} km")
        pts = _points_text(by_code.get(m.code), profile)
        if pts:
            bits.append(pts)
        items.append({
            "id": m.code,
            "title": program_name(m.code, lang, matches),
            "secondary_text": " · ".join(bits),
            "primary_cta": {"label": c["details"], "selection": {"action": "details", "code": m.code}},
        })
    return {
        "title": c["carousel_title"].format(child=_child(profile)),
        "subtitle": c["carousel_subtitle"],
        "items": items,
    }


def school_detail(match: SchoolMatch, profile: FamilyProfile, estimate: PointsEstimate | None = None) -> dict:
    c, lang = _c(profile), profile.language
    school = schools().get(match.code, {})
    rows = [{"label": c["row_theme"], "value": match.theme}]
    if match.address or school.get("address"):
        rows.append({"label": c["row_address"], "value": match.address or school["address"]})
    if match.phone or school.get("phone"):
        rows.append({"label": c["row_phone"], "value": match.phone or school["phone"]})
    rows.append({"label": c["row_bus"], "value": school.get("transport_note") or c["bus_unknown"]})
    pts = _points_text(estimate, profile)
    if pts:
        rows.append({"label": c["row_points"], "value": pts})
    return {
        "title": program_name(match.code, lang, [match]),
        "summary_rows": rows,
        "ctas": [
            {"label": c["add"], "selection": {"action": "add", "code": match.code}, "primary": True},
            {"label": c["back"], "selection": {"action": "back"}},
        ],
    }


def packet_review(plan: ActionPlan, profile: FamilyProfile, codes: list[str]) -> dict:
    c, lang = _c(profile), profile.language
    rows = [{"label": c["row_schools"],
             "value": " → ".join(f"{i}. {program_name(code, lang)}" for i, code in enumerate(codes, 1))}]
    deadline = next((d for d in plan.deadlines if d.key == "on_time_deadline" and d.date), None)
    if deadline:
        rows.append({"label": c["row_deadline"], "value": format_date(deadline.date, lang)})
    rows.append({"label": c["row_docs"], "value": c["docs_value"].format(n=len(plan.checklist))})
    rows.append({"label": c["row_email"], "value": plan.email_subject})
    if plan.coordinator_phone:
        rows.append({"label": c["row_school_phone"], "value": plan.coordinator_phone})
    return {
        "title": c["review_title"],
        "summary_rows": rows,
        "approve_cta": {"label": c["approve"], "selection": {"action": "approve_packet"}, "primary": True},
        "reject_cta": {"label": c["reject"], "selection": {"action": "reject_packet"}},
    }


# ---------------------------------------------------------------------------
# Validation (the schema ASI:One documents; unknown kinds/fields are dropped silently there)
# ---------------------------------------------------------------------------


def _cta_errors(cta, where: str) -> list[str]:
    if not isinstance(cta, dict) or not isinstance(cta.get("label"), str) or not cta["label"]:
        return [f"{where}: needs a non-empty label"]
    if not isinstance(cta.get("selection"), dict):
        return [f"{where}: selection must be an object"]
    return []


def _rows_errors(rows) -> list[str]:
    if not isinstance(rows, list) or not rows:
        return ["summary_rows must be a non-empty list"]
    return [f"summary_rows[{i}] needs string label and value" for i, r in enumerate(rows)
            if not (isinstance(r, dict) and isinstance(r.get("label"), str) and isinstance(r.get("value"), str))]


def validate(kind: str, payload: dict) -> list[str]:
    errors = []
    if kind not in KINDS:
        return [f"unknown card_kind {kind!r}"]
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_PAYLOAD_BYTES:
        errors.append("payload over 64 KB")
    if not isinstance(payload.get("title"), str) or not payload["title"]:
        errors.append("title must be a non-empty string")
    if kind == "carousel":
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            errors.append("items must be a non-empty list")
        for i, item in enumerate(items or []):
            if not isinstance(item.get("id"), str) or not isinstance(item.get("title"), str):
                errors.append(f"items[{i}] needs string id and title")
            errors += _cta_errors(item.get("primary_cta"), f"items[{i}].primary_cta")
    elif kind == "detail":
        errors += _rows_errors(payload.get("summary_rows"))
        for i, cta in enumerate(payload.get("ctas") or []):
            errors += _cta_errors(cta, f"ctas[{i}]")
    elif kind == "review":
        errors += _rows_errors(payload.get("summary_rows"))
        errors += _cta_errors(payload.get("approve_cta"), "approve_cta")
        errors += _cta_errors(payload.get("reject_cta"), "reject_cta")
    return errors


# ---------------------------------------------------------------------------
# Messages and click handling
# ---------------------------------------------------------------------------


def card_message(narration: str, kind: str | None = None, payload: dict | None = None) -> ChatMessage:
    """A chat reply with the narration text, plus a card when cards are on and the payload is valid."""
    content = [TextContent(type="text", text=narration)]
    if kind and payload is not None and cards_enabled() and not validate(kind, payload):
        content.append(MetadataContent(type="metadata", metadata={
            "card_protocol_version": "1",
            "requires_card_interaction": "true",
            "card_kind": kind,
            "card_payload": json.dumps(payload, ensure_ascii=False),
        }))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


@dataclass
class CardReply:
    narration: str
    kind: str | None = None
    payload: dict | None = None
    add_code: str | None = None      # orchestrator appends this to profile.intended_choices
    show_email: bool = False         # orchestrator sends the full email text


def respond_to_selection(selection: dict, state: SessionState) -> CardReply | None:
    """Turn a card click (from core.intent.parse_card_selection) into the next reply; None = not a card action."""
    p, c = state.profile, _c(state.profile)
    action, code = selection.get("action"), selection.get("code")
    match = next((m for m in state.matches if m.code == code), None)
    estimate = next((e for e in state.estimates if e.code == code), None)

    if action in ("details", None) and match:
        detail = school_detail(match, p, estimate)
        return CardReply(narration=detail["title"], kind="detail", payload=detail)
    if action == "add" and code:
        name = program_name(code, p.language, state.matches)
        if code in p.intended_choices:
            return CardReply(narration=c["already"].format(name=name))
        return CardReply(narration=c["added"].format(name=name), add_code=code)
    if action == "back" and state.matches:
        return CardReply(narration=c["carousel_subtitle"], kind="carousel",
                         payload=matches_carousel(state.matches, p, state.estimates))
    if action == "approve_packet" and state.plan:
        return CardReply(narration=state.plan.email_body, show_email=True)
    if action == "reject_packet":
        return CardReply(narration=c["change_q"])
    return None
