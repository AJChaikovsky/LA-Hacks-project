"""Coordinator email draft in the parent's language, with an English copy and a template fallback.

Owner: Dev C, ticket C5 (#23).

    draft_email(profile, codes, lang=None) -> (subject, body)

Called by core/paperwork.build_plan (Dev B, B6) to fill ActionPlan.email_subject/body.
Writes to the coordinator of the 1st choice. The email always asks the same 3 things:
IEP supports (naming iep_notes when known), tour dates, and bus eligibility. It never
mentions points or odds. The LLM (asi1) writes it; any draft that breaks those rules
is replaced by the template. With DEMO_MODE=true the first good draft per family+school
is cached in .cache/ so the demo email never changes between runs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel

from core import llm
from core.extract import norm
from core.render import program_name
from core.types import FamilyProfile

log = logging.getLogger("email")

CACHE = Path(__file__).resolve().parent.parent / ".cache" / "email_cache.json"
MAX_WORDS = 150

_ORDINAL_ES = {1: "primer", 2: "segundo", 3: "tercer", 4: "cuarto", 5: "quinto", 6: "sexto", 7: "séptimo",
               8: "octavo", 9: "noveno", 10: "décimo", 11: "undécimo", 12: "duodécimo"}


def _ordinal_en(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ---------------------------------------------------------------------------
# Template (the fallback, and the shape the LLM must follow)
# ---------------------------------------------------------------------------


def _school_parts(code: str, lang: str) -> tuple[str, str]:
    name = program_name(code, lang)
    school, _, theme = name.partition(" · ")
    return school or name, theme


def template(profile: FamilyProfile, code: str, lang: str) -> tuple[str, str]:
    school, theme = _school_parts(code, lang)
    child = profile.child_name
    grade = profile.applying_grade
    home = profile.home_neighborhood
    signer = profile.parent_name or ("[Su nombre]" if lang == "es" else "[Your name]")
    notes = f" ({profile.iep_notes})" if profile.iep_notes else ""

    if lang == "es":
        program = f"su programa {theme}" if theme else "su programa magnet"
        subject = f"Preguntas sobre el programa {theme or 'magnet'} de {school}" + (" (estudiante con IEP)" if profile.iep else "")
        who = child or "mi hijo o hija"
        intro = f"Le escribo sobre {who}, que entra a {_ORDINAL_ES.get(grade, '')} grado el próximo año".replace(" a  grado", "")
        intro += " y tiene un IEP." if profile.iep else "."
        likes = " Le encanta el arte." if "arts" in profile.interests else ""
        q1 = (f"¿Qué apoyos de educación especial ofrece el programa para estudiantes con IEP{notes}?" if profile.iep
              else "¿Qué apoyos ofrece el programa a estudiantes nuevos?")
        q3 = f"¿Hay transporte escolar desde {home}?" if home else "¿Hay transporte escolar?"
        body = (f"Estimado/a coordinador/a del programa magnet de {school}:\n\n"
                f"{intro}{likes} Estamos considerando {program}.\n\n"
                f"1. {q1}\n2. ¿Cuándo hay recorridos o sesiones informativas?\n3. {q3}\n\n"
                f"Gracias por su tiempo.\n{signer}")
    else:
        program = f"your {theme} program" if theme else "your magnet program"
        subject = f"Questions about the {theme or 'magnet'} program at {school}" + (" (student with an IEP)" if profile.iep else "")
        who = child or "my child"
        intro = f"I'm writing about {who}, who will start {_ordinal_en(grade)} grade next year" if grade else f"I'm writing about {who}"
        intro += " and has an IEP." if profile.iep else "."
        likes = " They love art." if "arts" in profile.interests else ""
        q1 = (f"What special education supports does the program offer students with an IEP{notes}?" if profile.iep
              else "What supports does the program offer new students?")
        q3 = f"Is there school transportation from {home}?" if home else "Is there school transportation?"
        body = (f"Dear magnet coordinator at {school},\n\n"
                f"{intro}{likes} We are considering {program}.\n\n"
                f"1. {q1}\n2. When are tours or information sessions?\n3. {q3}\n\n"
                f"Thank you for your time.\n{signer}")
    return subject, body


# ---------------------------------------------------------------------------
# Rules every draft must pass
# ---------------------------------------------------------------------------

_FORBIDDEN = re.compile(r"\bpuntos?\b|\bpoints?\b|%|probabilidad|posibilidad|\bodds\b|\bchances?\b|garantiz|guarantee")
# The profile never records the parent's role or anyone's gender, so a draft must not assume one.
_ROLE = re.compile(r"\b(madre|mama|padre|papa|mother|father|mom|dad|esposa|esposo|wife|husband)\b")
_TOPICS = {
    "iep": re.compile(r"\biep\b|\bpei\b|educacion especial|special education"),
    "tour": re.compile(r"recorrido|visita|sesion(es)? informativa|tour|information session|open house"),
    "bus": re.compile(r"transporte|autobus|\bbus\b|transportation"),
}


def problems(body: str, profile: FamilyProfile) -> list[str]:
    t = norm(body)
    issues = []
    if len(body.split()) > MAX_WORDS:
        issues.append(f"over {MAX_WORDS} words")
    if _FORBIDDEN.search(t):
        issues.append("mentions points or odds")
    if _ROLE.search(t):
        issues.append("assumes the parent's role")
    for topic, pattern in _TOPICS.items():
        if topic == "iep" and not profile.iep:
            continue
        if not pattern.search(t):
            issues.append(f"missing {topic} question")
    return issues


# ---------------------------------------------------------------------------
# LLM draft + cache
# ---------------------------------------------------------------------------


class Draft(BaseModel):
    subject: str = ""
    body: str = ""
    body_en: str = ""


_SYSTEM = (
    "Write a short, polite email from a parent to the magnet coordinator of an LAUSD middle school. "
    "Plain words, under 120 words. Ask exactly three numbered questions: (1) what special education "
    "supports the program offers students with an IEP (mention the services named, if any), (2) when "
    "tours or information sessions are, (3) whether there is school bus transportation from the family's "
    "neighborhood. Never mention points, odds, chances, or admission. Do not invent facts. "
    "Do not state the parent's relationship to the child (mother, father) or anyone's gender unless the "
    "facts say so; open with 'Estimado/a coordinador/a' in Spanish or 'Dear coordinator' in English. "
    "Return subject and body in the requested language, and body_en as a faithful English translation "
    "of body. Do not sign the email and do not introduce the writer; the signature is added later."
)


def _facts(profile: FamilyProfile, code: str, lang: str) -> str:
    school, theme = _school_parts(code, lang)
    return json.dumps({
        "language": "Spanish" if lang == "es" else "English",
        "school": school, "program": theme or "magnet",
        "child_name": profile.child_name, "grade_next_year": profile.applying_grade,
        "has_iep": bool(profile.iep), "iep_services": profile.iep_notes,
        "interests": profile.interests, "neighborhood": profile.home_neighborhood,
    }, ensure_ascii=False)  # no parent name: the model guesses gender/role from it; we sign in code


def _cache_key(profile: FamilyProfile, code: str, lang: str) -> str:
    return hashlib.sha256(_facts(profile, code, lang).encode()).hexdigest()[:16]


def _read_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(key: str, subject: str, body: str) -> None:
    data = _read_cache()
    data[key] = {"subject": subject, "body": body}
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("email cache not written: %s", exc)


def _with_english_copy(body: str, english: str, lang: str) -> str:
    if lang == "en" or not english.strip():
        return body
    return f"{body}\n\n---\nEnglish:\n{english.strip()}"


def draft_email(profile: FamilyProfile, codes: list[str], lang: str | None = None) -> tuple[str, str]:
    """(subject, body) for the coordinator of codes[0]. Never raises."""
    lang = lang if lang in ("es", "en") else profile.language
    code = codes[0] if codes else (profile.intended_choices or [""])[0]
    t_subject, t_body = template(profile, code, lang)
    _, t_body_en = template(profile, code, "en")
    fallback = (t_subject, _with_english_copy(t_body, t_body_en, lang))
    if not code:
        return fallback

    demo = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "on")
    key = _cache_key(profile, code, lang)
    if demo and key in _read_cache():
        cached = _read_cache()[key]
        return cached["subject"], cached["body"]

    if os.getenv("EMAIL_LLM", "on").lower() == "off":
        return fallback
    draft = llm.complete_json(
        f"Facts: {_facts(profile, code, lang)}", Draft, Draft(),
        system=_SYSTEM, model=os.getenv("EMAIL_MODEL", "asi1-mini"),
    )
    issues = problems(draft.body, profile) if draft.body else ["empty"]
    if lang != "en":
        issues += [f"english copy: {i}" for i in (problems(draft.body_en, profile) if draft.body_en else ["empty"])]
    if issues or not draft.subject.strip():
        log.warning("[EMAIL] using template: %s", issues or "no subject")
        return fallback

    signer = profile.parent_name or ("[Su nombre]" if lang == "es" else "[Your name]")
    body = f"{draft.body.strip()}\n{signer}"
    english = f"{draft.body_en.strip()}\n{profile.parent_name or '[Your name]'}" if draft.body_en.strip() else ""
    result = (draft.subject.strip(), _with_english_copy(body, english, lang))
    if demo:
        _write_cache(key, *result)
    return result
