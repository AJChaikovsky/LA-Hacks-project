"""Render codes (reasons, warnings, points, plan) into Spanish or English using i18n/*.json.

Owner: Dev C, ticket C3 (#21).

    render_matches(matches, profile) -> str
    render_points(estimates, profile) -> str
    render_warnings(warnings, profile) -> str
    render_plan(plan, profile) -> str

Every fact in a reply comes from the input models; this module only chooses words.
No percentages, ever. Points replies always end with the official disclaimer.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from urllib.parse import quote, urlencode

from core.config import load_config
from core.types import (
    ActionPlan,
    FamilyProfile,
    GuardrailWarning,
    PointsEstimate,
    SchoolMatch,
)

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "i18n"
SCHOOLS = ROOT / "data" / "schools.json"

# Reasons that are true for every school; shown once under the list instead of per school.
_GENERAL_REASONS = ("no_vapa_nearby", "iep_eligible")
_SEVERITY_ORDER = {"block": 0, "warn": 1, "info": 2}


@cache
def strings(lang: str) -> dict:
    lang = lang if lang in ("es", "en") else "en"
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


def _s(profile: FamilyProfile) -> dict:
    return strings(profile.language)


def _child(profile: FamilyProfile) -> str:
    return profile.child_name or _s(profile)["child_default"]


@cache
def schools() -> dict[str, dict]:
    """data/schools.json (Dev B, B1) by program code: the source of truth for official school names."""
    try:
        return {s["code"]: s for s in json.loads(SCHOOLS.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def program_name(code: str, lang: str, matches: list[SchoolMatch] | None = None) -> str:
    """'Belvedere MS · STEAM': official name from data/schools.json, program label from i18n."""
    m = next((m for m in matches or [] if m.code == code), None)
    record = schools().get(code, {})
    name = record.get("name") or (m.name if m else None)
    label = strings(lang)["program_labels"].get(code) or record.get("theme") or (m.theme if m else None)
    if not name:
        return code
    return f"{name} · {label}" if label else name


def place(name: str | None, lang: str) -> str:
    """A neighborhood as it should read in this language ('Downtown' -> 'el centro de Los Ángeles')."""
    if not name:
        return ""
    return strings(lang)["places"].get(name, name)


def _names(codes: list[str], lang: str) -> str:
    joiner = " y " if lang == "es" else " and "
    parts = [program_name(c, lang) for c in codes]
    return joiner.join(parts) if len(parts) <= 2 else ", ".join(parts[:-1]) + joiner + parts[-1]


def format_date(d: date, lang: str) -> str:
    s = strings(lang)
    return s["date_format"].format(d=d.day, month=s["months"][d.month - 1], y=d.year)


_LINK_TARGET = re.compile(r"\]\([^)\s]*\)")


def visible_text(markdown: str) -> str:
    """What the parent reads: markdown link targets (URLs with %20 etc.) removed, link text kept."""
    return _LINK_TARGET.sub("]", markdown)


def _fmt(template: str, **params) -> str:
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


# ---------------------------------------------------------------------------


def _grade_word(grade: int | None, s: dict) -> str:
    return s["matches"]["ordinals"].get(str(grade), str(grade)) if grade else ""


def covered_grades() -> tuple[int, int]:
    """Lowest and highest grade any program in data/schools.json serves."""
    grades = [g for record in schools().values() for g in record.get("grades", [])]
    return (min(grades), max(grades)) if grades else (6, 8)


def render_no_matches(profile: FamilyProfile) -> str:
    """Honest reply when nothing on our list fits: say what we cover, point to the official site."""
    s, child = _s(profile), _child(profile)
    low, high = covered_grades()
    grade = profile.applying_grade
    words = dict(child=child, low=_grade_word(low, s), high=_grade_word(high, s), grade=_grade_word(grade, s))
    key = "none_for_grade" if grade is not None and not low <= grade <= high else "none_fit"
    return "\n".join([_fmt(s["matches"][key], **words), "", s["matches"]["official_help"], "",
                      _fmt(s["matches"]["confirm_grade"], child=child)])


def render_ask_grade(profile: FamilyProfile) -> str:
    """Ask for the grade instead of assuming middle school."""
    return _fmt(_s(profile)["matches"]["ask_grade"], child=_child(profile))


def _matching_interest(profile: FamilyProfile, theme: str) -> str | None:
    """The parent's interest that fits this program's theme best (for the theme_match sentence)."""
    if not profile.interests:
        return None
    theme_map = load_config("matcher").get("theme_map", {})
    return max(profile.interests, key=lambda i: theme_map.get(i, {}).get(theme, 0.0))


def render_matches(matches: list[SchoolMatch], profile: FamilyProfile) -> str:
    if not matches:
        return render_no_matches(profile)
    s, child = _s(profile), _child(profile)
    intro = s["matches"]["intro_one"] if len(matches) == 1 else s["matches"]["intro"]
    lines = [_fmt(intro, n=len(matches), child=child), ""]
    general: list[str] = []
    for i, m in enumerate(matches, 1):
        interest = _matching_interest(profile, m.theme)
        ctx = dict(child=child, home=place(profile.home_neighborhood, profile.language),
                   work=place(profile.parent_work_area, profile.language),
                   interest=s["interests"].get(interest, "") if interest else "")
        lines.append(f"**{i}. {program_name(m.code, profile.language, matches)}**")
        own = [r for r in m.reason_codes if r not in _GENERAL_REASONS]
        general += [r for r in m.reason_codes if r in _GENERAL_REASONS and r not in general]
        lines += [f"- {_fmt(s['reasons'][r], **ctx)}" for r in own[:3]]
        contact = " · ".join(x for x in (m.address, m.phone) if x)
        if contact:
            lines.append(f"- {contact}")
        lines.append("")
    if general:
        ctx = dict(child=child)
        lines.append(s["matches"]["general_header"])
        lines += [f"- {_fmt(s['reasons'][r], **ctx)}" for r in general]
        lines.append("")
    lines.append(_fmt(s["matches"]["next"], child=child))
    return "\n".join(lines)


def _total_phrase(e: PointsEstimate, s: dict) -> str:
    total = sum(line.points for line in e.lines if line.status != "unknown")
    assumed = any(line.status == "assumed" and line.points for line in e.lines)
    key = "total_assumed" if assumed else "total_confirmed"
    return s["points"][key].format(total=total)


def _signature(e: PointsEstimate) -> tuple:
    return tuple((line.factor, line.points, line.status) for line in e.lines)


def render_points(estimates: list[PointsEstimate], profile: FamilyProfile) -> str:
    s, child, lang = _s(profile), _child(profile), profile.language
    p = s["points"]
    magnets = [e for e in estimates if e.applicable]
    others = [e for e in estimates if not e.applicable]
    lines = [_fmt(p["header"], child=child)]

    if magnets:
        if len({_signature(e) for e in magnets}) == 1:
            lines.append(_fmt(p["same_all"], n=len(magnets), total_phrase=_total_phrase(magnets[0], s)))
        else:
            lines += [_fmt(p["per_school"], school=program_name(e.code, lang), total_phrase=_total_phrase(e, s))
                      for e in magnets]
        # Explain the points that count, then ask about the unknowns.
        first = magnets[0]
        for line in first.lines:
            if line.points and line.status != "unknown":
                maybe = p["maybe"] if line.status == "assumed" else ""
                lines.append(_fmt(p["line"], factor=p["factors"][line.factor], points=line.points, maybe=maybe))
        questions = [_fmt(p["questions"][line.factor], child=child) for line in first.lines if line.status == "unknown"]
        if questions:
            lines += ["", *questions]

    lines += ["", p["competition_header"]]
    for e in magnets:
        level = s["competitiveness"][e.competitiveness]
        school = program_name(e.code, lang)
        if e.apps_per_opening and e.competitiveness == "low":
            lines.append(_fmt(p["ratio_low"], school=school, ratio=round(e.apps_per_opening, 1)))
        elif e.apps_per_opening:
            lines.append(_fmt(p["ratio"], school=school, ratio=round(e.apps_per_opening, 1), level=level))
        else:
            lines.append(_fmt(p["competition"], school=school, level=level))
    lines += [_fmt(p["not_applicable"], school=program_name(e.code, lang)) for e in others]

    lines += ["", p["grow"], "", p["next"], "", p["disclaimer"]]
    return "\n".join(lines)


def render_warnings(warnings: list[GuardrailWarning], profile: FamilyProfile) -> str:
    s, child, lang = _s(profile), _child(profile), profile.language
    w = s["warnings"]
    if not warnings:
        return "\n".join([w["ok"], "", w["next"]])
    lines = [w["header"], ""]
    for warning in sorted(warnings, key=lambda x: _SEVERITY_ORDER[x.severity]):
        params = warning.params
        text = _fmt(
            w[warning.code],
            child=child,
            limit=params.get("limit", 3),
            names=_names(params.get("codes", []), lang),
            name=program_name(params.get("first_choice") or params.get("suggest") or "", lang),
            district=params.get("district", ""),
        )
        lines.append(f"- {text}")
    lines += ["", w["next"]]
    return "\n".join(lines)


# Dates a parent must act on; each gets an "add to calendar" link.
REMINDER_KEYS = ("on_time_deadline", "correction_deadline", "accept_decline")


def reminder_links(plan: ActionPlan, lang: str) -> list[tuple[str, str]]:
    """[(date words, Google Calendar link)] for upcoming action dates. No names or address in the URL."""
    if os.getenv("REMINDER_LINKS", "on").lower() == "off":
        return []
    s = strings(lang)
    links = []
    for d in plan.deadlines:
        if d.key not in REMINDER_KEYS or d.date is None or (d.days_left is not None and d.days_left < 0):
            continue
        query = urlencode({
            "action": "TEMPLATE",
            "text": s["plan"]["reminder_title"].format(label=s["deadlines"][d.key]),
            "dates": f"{d.date:%Y%m%d}/{d.date + timedelta(days=1):%Y%m%d}",  # all-day event
            "details": s["plan"]["reminder_details"],
        }, quote_via=lambda v, *_: quote(v, safe="/"))
        links.append((format_date(d.date, lang), f"https://calendar.google.com/calendar/render?{query}"))
    return links


def render_plan(plan: ActionPlan, profile: FamilyProfile) -> str:
    s, child, lang = _s(profile), _child(profile), profile.language
    pl = s["plan"]
    lines = [pl["checklist_header"]]
    lines += [f"- [ ] {_fmt(s['checklist'][c], child=child)}" for c in plan.checklist]

    lines += ["", pl["deadlines_header"]]
    main_deadline = None
    for d in plan.deadlines:
        label = s["deadlines"][d.key]
        if d.date is None:
            lines.append(f"- {label[0].upper()}{label[1:]}")
            continue
        when = format_date(d.date, lang)
        left = ""
        if d.days_left is not None and d.days_left >= 0:
            left = f" ({pl['today'] if d.days_left == 0 else _fmt(pl['days_left'], n=d.days_left)})"
        item = f"{when}: {label}{left}"
        if d.key == "on_time_deadline":
            main_deadline = when
            item = f"**{item}**"
        lines.append(f"- {item}")
    reminders = reminder_links(plan, lang)
    if reminders:
        lines += ["", pl["reminders"] + " " + " · ".join(f"[{when}]({url})" for when, url in reminders)]

    lines += ["", pl["steps_header"]]
    lines += [f"{i}. {_fmt(s['steps'][c], child=child)}" for i, c in enumerate(plan.steps, 1)]

    if plan.warnings:
        lines += ["", render_warnings(plan.warnings, profile).rsplit("\n\n", 1)[0]]

    lines += ["", pl["email_header"], _fmt(pl["subject"], subject=plan.email_subject), ""]
    lines += [f"> {row}" if row else ">" for row in plan.email_body.split("\n")]
    if plan.coordinator_phone:
        # The number is the school's main line (data/schools.json), so name the school and say who to ask for.
        school = schools().get(plan.coordinator_code or "", {}).get("name")
        key = "coordinator_phone" if school else "coordinator_phone_no_name"
        lines += ["", _fmt(pl[key], name=school, phone=plan.coordinator_phone)]

    lines += ["", pl["no_submit"]]
    if main_deadline:
        lines.append(_fmt(pl["action"], date=main_deadline))
    return "\n".join(lines)
