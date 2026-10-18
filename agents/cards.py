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

from core.render import format_date, place, program_name, schools, strings
from core.school_facts import facts as school_facts
from core.types import ActionPlan, FamilyProfile, GuardrailWarning, PointsEstimate, SchoolMatch, SessionState

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
        item = {
            "id": m.code,
            "title": program_name(m.code, lang, matches),
            "secondary_text": " · ".join(bits),
            "primary_cta": {"label": c["details"], "selection": {"action": "details", "code": m.code}},
        }
        badges = school_badges(m, profile)
        if badges:
            item["badges"] = badges
        items.append(item)
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
    f = school_facts(match.code)  # B14: whole-school context from CDE, plain fractions only
    if f.get("enrollment"):
        rows.append({"label": c["row_students"], "value": c["facts_students"].format(n=f["enrollment"], year=f["year"])})
    for key, label in (("english_learners", "row_el"), ("iep", "row_iep")):
        if f.get(key):
            rows.append({"label": c[label], "value": c["facts_share"].format(n=f[key][0], d=f[key][1])})
    for t in match.transit[:2]:  # B13: direct Metro lines, scheduled and approximate
        rows.append({"label": c["row_metro"], "value": c["metro_value"].format(
            route=t.route, minutes=t.minutes, home=place(profile.home_neighborhood, lang), stop=t.alight_stop or "—")})
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


BADGE_VARIANTS = ("info", "success", "warning")
MAX_BADGES = 3
MAX_MAGNETS = 3


def school_badges(match: SchoolMatch, profile: FamilyProfile) -> list[dict]:
    """Short tags for a carousel item, from the match's reasons and the official seat data."""
    c, s = _c(profile), strings(profile.language)
    badges = []
    reasons = set(match.reason_codes)
    if reasons & {"theme_match", "arts_in_steam", "arts_in_media"} and profile.interests:
        badges.append({"label": s["interests"].get(profile.interests[0], "").capitalize(), "variant": "info"})
    if "spanish_dle" in reasons:
        badges.append({"label": c["badge_bilingual"], "variant": "info"})
    if "near_home" in reasons:
        badges.append({"label": c["badge_near_home"], "variant": "success"})
    elif "near_work" in reasons:
        badges.append({"label": c["badge_near_work"], "variant": "success"})
    record = schools().get(match.code, {})
    apps, seats = record.get("applications_2025_26"), record.get("openings_2026_27")
    if apps is not None and seats:
        if apps < seats:
            badges.append({"label": c["badge_low"], "variant": "success"})
        elif apps / seats >= 5:
            badges.append({"label": c["badge_very_high"], "variant": "warning"})
    return [b for b in badges if b["label"]][:MAX_BADGES]


def _is_magnet(code: str) -> bool:
    return schools().get(code, {}).get("program_type", "magnet") == "magnet"


def list_review(profile: FamilyProfile, codes: list[str], warnings: list[GuardrailWarning],
                matches: list[SchoolMatch] | None = None) -> dict:
    """The parent's list with one-tap fixes for what the guardrails found (detail card)."""
    c, lang = _c(profile), profile.language
    rows, magnets_seen = [], 0
    for n, code in enumerate(codes, 1):
        name = program_name(code, lang, matches)
        if _is_magnet(code):
            magnets_seen += 1
            label = c["list_choice"].format(n=magnets_seen) if magnets_seen <= MAX_MAGNETS else c["list_extra"]
        else:
            label = c["list_other"]
        rows.append({"label": label, "value": name})
    fix_codes: list[str] = []
    for w in warnings:
        if w.code in ("over_limit_magnet", "would_not_accept"):
            fix_codes += [x for x in w.params.get("codes", []) if x in codes and x not in fix_codes]
    ctas = [{"label": c["remove"].format(name=program_name(x, lang, matches)),
             "selection": {"action": "remove", "code": x}} for x in fix_codes]
    safety = next((w for w in warnings if w.code == "safety_option" and w.params.get("listed") is False), None)
    if safety and safety.params.get("code") and sum(_is_magnet(x) for x in codes if x not in fix_codes) < MAX_MAGNETS:
        ctas.append({"label": c["add_choice"].format(name=program_name(safety.params["code"], lang, matches)),
                     "selection": {"action": "add_choice", "code": safety.params["code"]}})
    ctas.append({"label": c["list_ok"], "selection": {"action": "list_ok"}, "primary": not fix_codes})
    if fix_codes:
        ctas[0]["primary"] = True
    return {"title": c["list_title"], "summary_rows": rows or [{"label": c["list_title"], "value": "-"}],
            "ctas": ctas[:4]}


def rank_form(profile: FamilyProfile, matches: list[SchoolMatch]) -> dict:
    """Pick 1st/2nd/3rd choice from the matches (form card); 2nd and 3rd are optional, as in Choices."""
    c, lang = _c(profile), profile.language
    options = [{"value": m.code, "label": program_name(m.code, lang, matches)} for m in matches]
    optional = [{"value": "none", "label": c["rank_none"]}] + options
    return {
        "title": c["rank_title"],
        "fields": [
            {"name": "first", "kind": "select", "label": c["rank_first"], "required": True, "options": options},
            {"name": "second", "kind": "select", "label": c["rank_second"], "required": False, "options": optional},
            {"name": "third", "kind": "select", "label": c["rank_third"], "required": False, "options": optional},
        ],
        "submit_cta": {"label": c["rank_submit"], "selection": {"action": "rank_list"}},
    }


def parse_rank_submission(selection: dict) -> list[str]:
    values = selection.get("values") if isinstance(selection.get("values"), dict) else selection
    picked = [str(values.get(k) or "").strip() for k in ("first", "second", "third")]
    return list(dict.fromkeys(x for x in picked if x and x != "none"))


FORM_FIELD_KINDS = ("text", "number", "email", "select", "checkbox")
MAX_FORM_GRADE = 11


def intake_form(profile: FamilyProfile) -> dict:
    """An ASI:One form asking only for what's missing (C9). The address is optional and never stored."""
    c, s = _c(profile), strings(profile.language)
    fields = []
    if profile.applying_grade is None:
        options = [{"value": "0", "label": c["kinder"]}] + [
            {"value": str(n), "label": c["grade_option"].format(n=n)} for n in range(1, MAX_FORM_GRADE + 1)]
        fields.append({"name": "grade", "kind": "select", "label": c["form_grade"], "required": True, "options": options})
    if not profile.home_neighborhood:
        fields.append({"name": "neighborhood", "kind": "text", "label": c["form_neighborhood"], "required": True})
    if not profile.interests:
        fields.append({"name": "interest", "kind": "select", "label": c["form_interest"], "required": False,
                       "options": [{"value": k, "label": v} for k, v in s["interests"].items()]})
    if profile.iep is None:
        fields.append({"name": "iep", "kind": "checkbox", "label": c["form_iep"], "required": False})
    fields.append({"name": "address", "kind": "text", "label": c["form_address"], "required": False})
    return {
        "title": c["form_title"].format(child=_child(profile)),
        "fields": fields,
        "submit_cta": {"label": c["form_submit"], "selection": {"action": "intake_form"}},
    }


_TRUE = {"true", "on", "yes", "si", "sí", "1", "checked"}


def parse_form_submission(selection: dict) -> tuple[dict, str | None]:
    """Form answers -> (profile updates, address). Unknown or blank answers are ignored."""
    from core.extract import find_places, norm, normalize_interests

    values = selection.get("values") if isinstance(selection.get("values"), dict) else selection
    updates: dict = {}
    grade = str(values.get("grade", "")).strip()
    if grade.isdigit() and 0 <= int(grade) <= 12:
        updates["current_grade"], updates["applying_grade"] = int(grade), int(grade) + 1
    hood = str(values.get("neighborhood", "")).strip()
    if hood:
        home, _ = find_places(norm(hood))
        updates["home_neighborhood"] = home or hood.title()
    interests = normalize_interests([str(values["interest"])]) if values.get("interest") else []
    if interests:
        updates["interests"] = interests
    if "iep" in values:
        updates["iep"] = str(values["iep"]).strip().lower() in _TRUE or values["iep"] is True
    address = str(values.get("address", "")).strip() or None
    return updates, address


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
            for b in item.get("badges", []):
                if not (isinstance(b, dict) and isinstance(b.get("label"), str) and b["label"]
                        and b.get("variant", "info") in BADGE_VARIANTS):
                    errors.append(f"items[{i}].badges needs a label and variant in {BADGE_VARIANTS}")
    elif kind == "detail":
        errors += _rows_errors(payload.get("summary_rows"))
        for i, cta in enumerate(payload.get("ctas") or []):
            errors += _cta_errors(cta, f"ctas[{i}]")
    elif kind == "form":
        fields = payload.get("fields")
        if not isinstance(fields, list) or not fields:
            errors.append("fields must be a non-empty list")
        for i, f in enumerate(fields or []):
            if not (isinstance(f, dict) and isinstance(f.get("name"), str) and isinstance(f.get("label"), str)):
                errors.append(f"fields[{i}] needs string name and label")
                continue
            if f.get("kind") not in FORM_FIELD_KINDS:
                errors.append(f"fields[{i}].kind must be one of {FORM_FIELD_KINDS}")
            if f.get("kind") == "select" and not (isinstance(f.get("options"), list) and f["options"] and all(
                    isinstance(o, dict) and isinstance(o.get("value"), str) and isinstance(o.get("label"), str)
                    for o in f["options"])):
                errors.append(f"fields[{i}]: select needs non-empty options with string value and label")
        errors += _cta_errors(payload.get("submit_cta"), "submit_cta")
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


def intake_message(profile: FamilyProfile) -> ChatMessage:
    """Ask for missing info: the text question plus the intake form card (text-only when CARDS=off)."""
    from core.render import render_ask_grade

    return card_message(render_ask_grade(profile), "form", intake_form(profile))


@dataclass
class CardReply:
    narration: str
    kind: str | None = None
    payload: dict | None = None
    add_code: str | None = None      # orchestrator appends this to profile.intended_choices
    show_email: bool = False         # orchestrator sends the full email text
    profile_updates: dict | None = None  # intake form answers -> merge into the profile, then match
    address: str | None = None       # for B11 geocoding only; never log or store it
    new_list: list[str] | None = None  # list fix / ranking form -> re-run the list checks on this list
    go_plan: bool = False            # "Mi lista está bien" -> build the plan


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
    current = list(p.intended_choices) or [m.code for m in state.matches]
    if action == "remove" and code:
        return CardReply(narration=c["removed"].format(name=program_name(code, p.language, state.matches)),
                         new_list=[x for x in current if x != code])
    if action == "add_choice" and code:
        return CardReply(narration="", new_list=list(dict.fromkeys(current + [code])))
    if action == "rank_list":
        ranked = parse_rank_submission(selection)
        return CardReply(narration="", new_list=ranked) if ranked else None
    if action == "list_ok":
        return CardReply(narration="", go_plan=True)
    if action == "intake_form":
        updates, address = parse_form_submission(selection)
        return CardReply(narration="", profile_updates=updates, address=address)
    return None
