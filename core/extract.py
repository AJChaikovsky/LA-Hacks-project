"""Free text -> FamilyProfile: deterministic es/en pre-pass, then LLM fill-in.

Owner: Dev C, ticket C1 (#19).

    extract(text, prior=None) -> FamilyProfile          (frozen signature, see core/stubs.py)
    parse_school_list(text, match_codes) -> (choices, doubtful)

Precedence when merging: prior < LLM < pre-pass. The pre-pass is regex/keyword code, so
the golden demo path works even when the LLM is down (EXTRACT_LLM=off skips it).
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel

from core import llm
from core.types import INTERESTS, FamilyProfile

log = logging.getLogger("extract")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def norm(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace. Keeps string length close to the original."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower())


_ES_WORDS = set("de la el que y en mi hijo hija esta tiene escuela grado por para con una los las del le lo "
                "quiero vivo trabajo cuanto como donde tengo hola gracias si pero porque muy tambien".split())
_EN_WORDS = set("the and my son daughter is has school grade to of in for with an what how where "
                "i we he she his her live work have want hi thanks but because very also".split())


def detect_language(text: str) -> str | None:
    words = re.findall(r"[a-z']+", norm(text))
    if re.search(r"[¿¡ñ]", text or ""):
        return "es"
    es = sum(w in _ES_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    if es == en:
        return None
    return "es" if es > en else "en"


# ---------------------------------------------------------------------------
# Grade
# ---------------------------------------------------------------------------

_ORDINALS = {
    "primero": 1, "primer": 1, "segundo": 2, "tercero": 3, "tercer": 3, "cuarto": 4, "quinto": 5,
    "sexto": 6, "septimo": 7, "octavo": 8, "noveno": 9,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9,
}
_GRADE_TOKEN = re.compile(
    r"\b(" + "|".join(sorted(_ORDINALS, key=len, reverse=True)) + r"|\d{1,2}(?:st|nd|rd|th|o|to|vo|ro|do|er)?)\b"
)
_APPLY_CUE = re.compile(
    r"(entra|entrar|entrara|pasa|pasar|para|going into|go into|goes into|entering|enter|will be in|"
    r"starting|start|applying for|apply for|for)\s+(?:a|al|en|in|to|the)?\s*$"
)
_CURRENT_CUE = re.compile(r"(esta en|cursa|is in|he'?s in|she'?s in|currently in|in)\s+(?:el|the)?\s*$")


def _grade_value(token: str) -> int | None:
    if token in _ORDINALS:
        return _ORDINALS[token]
    m = re.match(r"\d{1,2}", token)
    v = int(m.group()) if m else None
    return v if v is not None and 1 <= v <= 12 else None


def find_grades(t: str) -> tuple[int | None, int | None]:
    """(current_grade, applying_grade) from normalized text."""
    current = applying = None
    for m in _GRADE_TOKEN.finditer(t):
        tok = m.group(1)
        value = _grade_value(tok)
        if value is None:
            continue
        before = t[max(0, m.start() - 30):m.start()]
        after = t[m.end():m.end() + 10]
        has_grade_word = after.lstrip().startswith("grad") or before.rstrip().endswith("grade")
        is_word_or_suffixed = tok in _ORDINALS or not tok.isdigit()
        apply_cue = _APPLY_CUE.search(before)
        current_cue = _CURRENT_CUE.search(before)
        if not (has_grade_word or (is_word_or_suffixed and (apply_cue or current_cue))):
            continue
        if apply_cue and not current_cue:
            applying = applying or value
        else:
            current = current or value
    if current and not applying:
        applying = current + 1
    if applying and not current:
        current = applying - 1
    return current, applying


# ---------------------------------------------------------------------------
# Interests, IEP, flags
# ---------------------------------------------------------------------------

_INTEREST_KEYWORDS = [  # (regex on normalized text, tag); order matters (medio ambiente before medios)
    (r"medio ambiente|environment|naturaleza|nature|ecolog", "environment"),
    (r"\barte|\bartes\b|dibuj|pint[ao]|\bart\b|\barts\b|draw|paint|music|musica|danza|\bdance|teatro|theat", "arts"),
    (r"cienci|science|matematic|\bmath|tecnolog|technolog|robot|ingenier|engineer|computa|coding|programar", "stem"),
    (r"medicina|medic[oa]|doctor|enfermer|nurse|salud|health", "medicine"),
    (r"abogad|lawyer|\blaw\b|\bley\b|policia|police", "law"),
    (r"\bvideo|\bcine\b|\bfilm|fotograf|photograph|\bmedios\b|\bmedia\b|youtube", "media"),
    (r"idioma|bilingu|languages", "languages"),
    (r"deporte|sport|futbol|soccer|basketball|basquet|beisbol|baseball", "sports"),
    (r"negocio|business|emprend|entrepreneur", "business"),
]


def find_interests(t: str) -> list[str]:
    found = []
    for pattern, tag in _INTEREST_KEYWORDS:
        if re.search(pattern, t) and tag not in found:
            found.append(tag)
    return found


def normalize_interests(values: list[str]) -> list[str]:
    """Map free-form interest strings (e.g. the LLM's 'arte') onto the frozen Interest codes."""
    out = []
    for v in values:
        tags = [norm(v)] if norm(v) in INTERESTS else find_interests(norm(v))
        out += [t for t in tags if t not in out]
    return out


def find_iep(t: str) -> bool | None:
    if re.search(r"\b(no tiene|sin|doesn'?t have|does not have|has no|no)\s+(un |an |a )?(iep|pei)\b", t):
        return False
    if re.search(r"\b(iep|pei)\b|educacion especial|special ed", t):
        return True
    return None


def find_flags(t: str) -> dict:
    flags: dict = {}
    if re.search(r"aprendiz de ingles|aprendiendo ingles|aprende ingles|english learner|learning english|\bell\b", t):
        flags["english_learner"] = True
    if re.search(r"dotad|superdotad|\bgate\b|gifted", t):
        flags["gifted"] = True
    if re.search(r"(ya esta|esta|already|currently) (en|in) (un |una |a )?(programa )?magnet", t):
        flags["in_magnet_now"] = True
    if re.search(r"solo tengo (mi |un )?(celular|telefono)|only (have )?(a |my )?(phone|cell)", t):
        flags["device"] = "phone"
    m = re.search(r"lista de espera (?:por |durante )?(\d)\s*anos|waitlist(?:ed)? (?:for )?(\d) years", t)
    if m:
        flags["waitlist_years"] = int(m.group(1) or m.group(2))
    return flags


_NAME_STOP = {"esta", "tiene", "is", "has", "va", "and", "y", "que", "who", "es", "le", "loves"}


def find_parent_name(text: str) -> str | None:
    m = re.search(r"\b(?:[Ss]oy|[Mm]e llamo|[Mm]i nombre es|I'?m|I am|[Mm]y name is)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)", text or "")
    if m and norm(m.group(1)) not in _NAME_STOP | {"la", "el", "su", "de", "mama", "papa", "mom", "dad", "the"}:
        return m.group(1)
    return None


def find_child_name(text: str) -> str | None:
    m = re.search(
        r"\b(?:[Mm]i hij[oa]|[Mm]y (?:son|daughter|kid|child)|se llama|[Hh](?:is|er) name is)\s+"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
        text or "",
    )
    if m and norm(m.group(1)) not in _NAME_STOP:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Places (home vs work)
# ---------------------------------------------------------------------------

_BUILTIN_PLACES = {
    "Boyle Heights": ["boyle heights"],
    "East Los Angeles": ["east los angeles", "east la", "este de los angeles", "el este"],
    "El Sereno": ["el sereno"],
    "Lincoln Heights": ["lincoln heights"],
    "Highland Park": ["highland park"],
    "Downtown": ["downtown", "dtla", "el centro", "centro de los angeles", "centro de la ciudad"],
    "City Terrace": ["city terrace"],
    "Echo Park": ["echo park"],
    "Pico-Union": ["pico union", "pico-union"],
    "South LA": ["south la", "south los angeles", "sur de los angeles"],
    "Huntington Park": ["huntington park"],
    "Cypress Park": ["cypress park"],
}


def load_places() -> dict[str, list[str]]:
    """Built-in aliases, plus any names in data/places.json (Dev B, B1) when it exists."""
    places = {k: list(v) for k, v in _BUILTIN_PLACES.items()}
    path = DATA_DIR / "places.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("neighborhoods"), dict):
                raw = raw["neighborhoods"]  # B1 shape: {"neighborhoods": {name: {lat, lng, aliases}}}
            items = raw.items() if isinstance(raw, dict) else [(p.get("name"), p) for p in raw]
            for name, info in items:
                if not name:
                    continue
                aliases = info.get("aliases", []) if isinstance(info, dict) else []
                places.setdefault(name, []).extend(norm(a) for a in [name, *aliases])
        except (ValueError, AttributeError) as exc:
            log.warning("data/places.json unreadable, using built-in places: %s", exc)
    return places


_WORK_CUE = re.compile(r"trabaj\w*|work\w*|\bjob\b|empleo|oficina|office")
_HOME_CUE = re.compile(r"\bviv\w*|\blive\w*|living|somos de|we'?re from|we are from|\bfrom\b|\bcasa\b|\bhome\b")


def find_places(t: str) -> tuple[str | None, str | None]:
    """(home_neighborhood, parent_work_area) from normalized text."""
    home = work = None
    for name, aliases in load_places().items():
        for alias in aliases:
            for m in re.finditer(r"\b" + re.escape(alias) + r"\b", t):
                before = t[max(0, m.start() - 35):m.start()]
                w = max((c.end() for c in _WORK_CUE.finditer(before)), default=-1)
                h = max((c.end() for c in _HOME_CUE.finditer(before)), default=-1)
                if w > h:
                    work = work or name
                else:
                    home = home or name
    return home, work


# ---------------------------------------------------------------------------
# Schools named in free text (sync codes with data/schools.json, Dev B)
# ---------------------------------------------------------------------------

_SCHOOLS = [  # (aliases, default code, {theme keyword: code})
    (["bel vedere", "belvedere"], "1804703",
     {"steam": "1804703", "media": "1804702", "medios": "1804702", "comunicac": "1804702"}),
    (["hollenbeck"], "1817902",
     {"stem": "1817902", "medic": "1817902", "law": "1817903", "ley": "1817903", "public service": "1817903",
      "servicio publico": "1817903"}),
    (["stevenson"], "1838701TWS",
     {"gifted": "1838702", "dotad": "1838702", "gate": "1838702", "dual": "1838701TWS",
      "bilingu": "1838701TWS", "inmersion": "1838701TWS"}),
    (["griffith"], "1816801", {}),
    (["los angeles academy", "la academy"], "1820002", {}),
    (["adams"], "1800903", {}),
    (["markham"], "1823703", {"health": "1823702", "salud": "1823702"}),
]


def find_school_mentions(t: str, match_codes: list[str] | None = None) -> list[tuple[int, str]]:
    """[(position, program code)] in text order."""
    match_codes = match_codes or []
    found = []
    for aliases, default, themes in _SCHOOLS:
        for alias in aliases:
            for m in re.finditer(r"\b" + re.escape(alias) + r"\b", t):
                window = t[max(0, m.start() - 20):m.end() + 25]
                code = next((c for kw, c in themes.items() if kw in window), None)
                if code is None:
                    school_codes = {default, *themes.values()}
                    code = next((c for c in match_codes if c in school_codes), default)
                found.append((m.start(), code))
    seen, ordered = set(), []
    for pos, code in sorted(found):
        if code not in seen:
            seen.add(code)
            ordered.append((pos, code))
    return ordered


_DOUBT_CUES = re.compile(
    r"por si acaso|no se si (lo|la) aceptaria|no (lo|la) aceptaria|queda (muy )?lej|lejisimos|muy lejos|"
    r"just in case|wouldn'?t accept|would not accept|not sure (if )?(i|we)'?d accept|too far|really far|"
    r"wouldn'?t send|no (lo|la) mandaria"
)


def find_doubtful(t: str, mentions: list[tuple[int, str]]) -> list[str]:
    """Each doubt phrase marks the nearest named school within 80 characters."""
    doubtful = []
    for cue in _DOUBT_CUES.finditer(t):
        near = [(min(abs(pos - cue.start()), abs(pos - cue.end())), code) for pos, code in mentions]
        near = [n for n in near if n[0] <= 80]
        if near:
            code = min(near)[1]
            if code not in doubtful:
                doubtful.append(code)
    return doubtful


_ALL_MATCHES = re.compile(r"\b(las|los|esas|estas|all|the|those|these) (tres|3|three)\b|\btodas\b|all of them")
_ONLY = re.compile(r"\b(solo|solamente|nada mas|only|just)\b")


def parse_school_list(text: str, match_codes: list[str]) -> tuple[list[str], list[str]]:
    """Resolve a listing message into (choices in rank order, doubtful codes).

    "Voy a poner las tres, y una más por si acaso: Markham..." with the 3 matched codes
    -> ([m1, m2, m3, "1823703"], ["1823703"]).
    """
    t = norm(text)
    mentions = find_school_mentions(t, match_codes)
    choices: list[str] = list(match_codes) if _ALL_MATCHES.search(t) else []
    if not (choices and _ONLY.search(t)):
        choices += [code for _, code in mentions if code not in choices]
    return choices, find_doubtful(t, mentions)


# ---------------------------------------------------------------------------
# Pre-pass + LLM + merge
# ---------------------------------------------------------------------------


def prepass(text: str) -> dict:
    """Deterministic facts found in `text`. Only keys it is sure about."""
    t = norm(text)
    out: dict = {}
    lang = detect_language(text)
    if lang:
        out["language"] = lang
    current, applying = find_grades(t)
    if current:
        out["current_grade"], out["applying_grade"] = current, applying
    interests = find_interests(t)
    if interests:
        out["interests"] = interests
    iep = find_iep(t)
    if iep is not None:
        out["iep"] = iep
    home, work = find_places(t)
    if home:
        out["home_neighborhood"] = home
    if work:
        out["parent_work_area"] = work
    name = find_child_name(text)
    if name:
        out["child_name"] = name
    parent = find_parent_name(text)
    if parent:
        out["parent_name"] = parent
    out.update(find_flags(t))
    if re.search(r"(hablamos|habla|speak|speaks) (en )?(espanol|spanish)", t):
        out["home_language"] = "Spanish"
    doubtful = find_doubtful(t, find_school_mentions(t))
    if doubtful:
        out["would_not_accept"] = doubtful
    return out


class LLMProfile(BaseModel):
    """What we ask the LLM for. Loose on purpose; results are normalized before merging."""

    language: str | None = None
    child_name: str | None = None
    current_grade: int | None = None
    applying_grade: int | None = None
    interests: list[str] = []
    iep: bool | None = None
    iep_notes: str | None = None
    english_learner: bool | None = None
    home_language: str | None = None
    home_neighborhood: str | None = None
    resident_school: str | None = None
    parent_work_area: str | None = None
    gifted: bool | None = None
    in_magnet_now: bool | None = None
    waitlist_years: int | None = None


SYSTEM_PROMPT = (
    "You read a parent's message about their child and the LAUSD Choices school application. "
    "Extract only facts the parent states. Never guess; omit anything not stated. "
    "current_grade is the grade the child is in now (kindergarten = 0); applying_grade is the grade "
    "they will enter. interests must use only these tags: " + ", ".join(INTERESTS) + " "
    "(drawing/painting/music/dance/theater -> arts). iep is true if the child has an IEP/PEI or special "
    "education services; iep_notes is a short note of any services mentioned. "
    "home_neighborhood is where the family lives; parent_work_area is where the parent works "
    "('el centro' = Downtown). language is 'es' if the parent writes in Spanish, else 'en'."
)


def llm_extract(text: str) -> dict:
    if os.getenv("EXTRACT_LLM", "on").lower() == "off":
        return {}
    model = os.getenv("EXTRACT_MODEL", "asi1-mini")
    result = llm.complete_json(text, LLMProfile, LLMProfile(), system=SYSTEM_PROMPT, model=model)
    data = result.model_dump(exclude_none=True)
    data["interests"] = normalize_interests(data.get("interests", []))
    if data.get("language") not in ("es", "en"):
        data.pop("language", None)
    # Drop filler like "Has an IEP": notes should name services, not restate the flag.
    if re.fullmatch(r"(he |she |the child )?(has|tiene)( an?| un)? (iep|pei)\.?", norm(data.get("iep_notes", "")).strip()):
        data.pop("iep_notes")
    for key in ("current_grade", "applying_grade"):
        if not (isinstance(data.get(key), int) and 0 <= data[key] <= 12):
            data.pop(key, None)
    return {k: v for k, v in data.items() if v not in ([], "")}


_LIST_FIELDS = ("interests", "would_not_accept", "sibling_program_codes")


def merge(prior: FamilyProfile, *layers: dict) -> FamilyProfile:
    """Later layers win for scalars; lists are unioned in order."""
    merged = prior.model_dump()
    for layer in layers:
        for key, value in layer.items():
            if key in _LIST_FIELDS:
                merged[key] = merged[key] + [v for v in value if v not in merged[key]]
            elif value is not None:
                merged[key] = value
    if merged.get("current_grade") is not None and merged.get("applying_grade") is None:
        merged["applying_grade"] = merged["current_grade"] + 1
    if merged.get("language") == "es" and not merged.get("home_language"):
        merged["home_language"] = "Spanish"
    return FamilyProfile.model_validate(merged)


def extract(text: str, prior: FamilyProfile | None = None) -> FamilyProfile:
    prior = prior or FamilyProfile()
    pre = prepass(text)
    from_llm = llm_extract(text)
    # Keep the grade pair consistent: if the pre-pass found grades, ignore the LLM's.
    if "current_grade" in pre:
        from_llm.pop("current_grade", None)
        from_llm.pop("applying_grade", None)
    profile = merge(prior, from_llm, pre)
    log.info("[EXTRACT] pre=%s llm=%s", sorted(pre), sorted(from_llm))
    return profile
