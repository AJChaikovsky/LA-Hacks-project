"""Ask-the-brochure agent: rule questions answered from the official brochure, with page citations.

Owner: Dev C, ticket C12 (#46).

    answer(question, lang) -> BrochureAnswer
    render_answer(result, lang) -> str

Retrieval is plain BM25 over data/brochure_passages.json (built by scripts/build_brochure_index.py
from the 2027-28 EN/SP brochures and the Choices FAQ); no new dependencies. Spanish questions
search the Spanish brochure. asi1-mini writes a short answer from the top passages only, and the
answer is rejected unless it cites one of them and every number in it appears in that passage.
Fallbacks: the best passage's most relevant sentences, quoted; below the relevance threshold, the
hotline instead of a guess.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from core import llm
from core.extract import detect_language, norm

log = logging.getLogger("brochure")

DATA = Path(__file__).resolve().parent.parent / "data" / "brochure_passages.json"
TOP_K = 5
MIN_SCORE = 4.0          # below this the brochure probably doesn't answer it
MAX_ANSWER_WORDS = 80

_STOP = set("""
a al algo como con cual cuales cuando de del donde el ella ellas ellos en es esa ese eso esta este esto la las
le les lo los mas me mi mis muy no nos o para pero por que se si sin sobre su sus te tiene tu un una uno unos y ya
yo hay ser son fue era puede pueden hacer hago va voy esta estan
the a an and are as at be but by can do does for from has have how i if in is it its me my no not of on or our
so that the their them then there they this to was we what when where which who will with would you your
""".split())

# A few bridges between how parents ask and how the brochure words it.
_SYNONYMS = {
    "contest": ["respue", "respond"], "respond": ["respon"], "oferta": ["ofrec", "lugar"], "offer": ["seat"],
    "rechaz": ["declin"], "declin": ["rechaz"], "tarde": ["tardia", "tardio"], "late": ["late"],
    "gemelo": ["melliz", "trilli"], "twins": ["triple"], "herman": ["herman"], "siblin": ["siblin"],
    "bus": ["transp", "autobu"], "autobu": ["transp"], "camion": ["transp", "autobu"],
    "poner": ["solici", "presen"], "aplica": ["solici"], "aplico": ["solici"], "apply": ["applic"],
    "cuanta": ["hasta", "tres"], "cuanto": ["hasta", "tres"], "many": ["up", "three"],
    "cambia": ["correg", "modifi", "actual"], "change": ["correc", "update"], "pierdo": ["pierde", "perder"],
    "lose": ["lose", "removed"], "escuel": ["progra"], "schools": ["progra"],
}


# Tokens are cut to 6 letters, so synonym keys must be too ("contest" -> "contes").
_SYN = {key[:6]: [v[:6] for v in values] for key, values in _SYNONYMS.items()}


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]{2,}", norm(text))
    return [w[:6] if len(w) > 6 else w for w in words if w not in _STOP]


@dataclass
class Passage:
    id: str
    lang: str
    page: int | None
    text: str
    source_url: str


@cache
def passages() -> tuple[Passage, ...]:
    try:
        raw = json.loads(DATA.read_text(encoding="utf-8"))["passages"]
    except (OSError, ValueError, KeyError):
        log.warning("data/brochure_passages.json missing; run scripts/build_brochure_index.py")
        return ()
    return tuple(Passage(p["id"], p["lang"], p.get("page"), p["text"], p["source_url"]) for p in raw)


@cache
def _index(lang: str):
    docs = [p for p in passages() if p.lang == lang] or list(passages())
    toks = [Counter(tokens(p.text)) for p in docs]
    df = Counter(t for c in toks for t in c)
    avg = sum(sum(c.values()) for c in toks) / max(len(toks), 1)
    return docs, toks, df, avg


def expand(question: str) -> tuple[list[str], list[str]]:
    """(the question's own tokens, plus synonym bridges to the brochure's wording)."""
    own = tokens(question)
    return own, own + [s for q in own for s in _SYN.get(q, [])]


def coverage(question: str, passage: Passage) -> int:
    """How many of the question's own words the passage covers (directly or via a synonym)."""
    own, _ = expand(question)
    seen = set(tokens(passage.text))
    return len({t for t in own if t in seen or any(x in seen for x in _SYN.get(t, []))})


def search(question: str, lang: str, k: int = TOP_K) -> list[tuple[float, Passage]]:
    """BM25 top candidates, reranked so passages covering more of the question come first."""
    ranked = _bm25(question, lang, k * 3)
    ranked.sort(key=lambda x: (-coverage(question, x[1]), -x[0]))
    return ranked[:k]


def _bm25(question: str, lang: str, k: int) -> list[tuple[float, Passage]]:
    docs, toks, df, avg = _index(lang)
    _, query = expand(question)
    n, k1, b = len(docs), 1.5, 0.75
    scored = []
    for doc, tf in zip(docs, toks):
        length = sum(tf.values()) or 1
        score = 0.0
        for q in set(query):
            if q in tf:
                idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
                score += idf * tf[q] * (k1 + 1) / (tf[q] + k1 * (1 - b + b * length / avg))
        if score:
            scored.append((score, doc))
    return sorted(scored, key=lambda x: -x[0])[:k]


@dataclass
class BrochureAnswer:
    found: bool
    text: str
    passage: Passage | None = None
    used_llm: bool = False


class _Draft(BaseModel):
    answer: str = ""
    passage_id: str = ""


_SYSTEM = (
    "You help a parent with the LAUSD Choices application. Below are numbered passages from the official "
    "brochure. Pick the ONE passage that best answers the question and answer from it in {language}, in at "
    "most 3 short plain sentences ('usted' in Spanish). Use only facts from that passage; no outside facts. "
    "Only if none of the passages is about the question, return an empty answer. "
    "Return JSON with keys answer and passage_id."
)


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _sentences(passage: Passage) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.?!])\s+", passage.text) if len(x.split()) >= 5]


def _best_quote(question: str, hits: list[Passage], limit: int = 2) -> tuple[str, Passage]:
    """Fallback: the sentences (across the top passages) that cover the most of the question."""
    q = set(expand(question)[1])
    scored = [(len(q & set(tokens(sent))), -i, sent, p) for i, p in enumerate(hits) for sent in _sentences(p)]
    if not scored:
        return hits[0].text, hits[0]
    best = max(scored)
    passage = best[3]
    ranked = sorted((x for x in scored if x[3] is passage), reverse=True)[:limit]
    keep = {x[2] for x in ranked}
    return " ".join(sent for sent in _sentences(passage) if sent in keep), passage


_SPANISH_SIGNAL = re.compile(r"[áéíóúñ¿¡]|\b(puede|usted|hasta|programas?|solicitud(es)?|escuelas?|tiene|para|los|"
                             r"las|del|una|que|segunda|tercera|opcion(es)?|lista|espera)\b", re.I)


def _wrong_language(text: str, lang: str) -> bool:
    if lang == "en":
        return len(_SPANISH_SIGNAL.findall(text)) >= 2
    return detect_language(text) == "en"


def _llm_answer(question: str, hits: list[Passage], lang: str) -> tuple[str, Passage] | None:
    if os.getenv("BROCHURE_LLM", "on").lower() == "off":
        return None
    context = "\n\n".join(f"[{p.id}] {p.text}" for p in hits)
    draft = llm.complete_json(
        f"Question: {question}\n\nPassages:\n{context}", _Draft, _Draft(),
        system=_SYSTEM.format(language="Spanish" if lang == "es" else "English"),
        model=os.getenv("BROCHURE_MODEL", "asi1-mini"),
    )
    pid = draft.passage_id.strip().strip("[]").strip()  # the model sometimes echoes "[en-p5-1]"
    used = next((p for p in hits if p.id == pid), None)
    text = draft.answer.strip()
    if not text or used is None:
        return None
    if len(text.split()) > MAX_ANSWER_WORDS or not _numbers(text) <= _numbers(used.text) | _numbers(question):
        log.warning("[BROCHURE] rejected LLM answer (length or unsupported number)")
        return None
    if _wrong_language(text, lang):
        log.warning("[BROCHURE] rejected LLM answer in the wrong language")
        return None
    return text, used


def answer(question: str, lang: str | None = None) -> BrochureAnswer:
    lang = lang if lang in ("es", "en") else (detect_language(question) or "en")
    hits = search(question, lang)
    own, _ = expand(question)
    # Relevance gate: a high score from one common word ("best pizza") isn't an answer.
    best_cover = max((coverage(question, p) for _, p in hits), default=0)
    if not hits or max(s for s, _ in hits) < MIN_SCORE or best_cover < min(2, len(set(own))):
        log.info("[BROCHURE] no confident passage for %r", question[:60])
        return BrochureAnswer(found=False, text="")
    top = [p for _, p in hits]
    drafted = _llm_answer(question, top, lang)
    if drafted:
        return BrochureAnswer(found=True, text=drafted[0], passage=drafted[1], used_llm=True)
    quote, passage = _best_quote(question, top)
    return BrochureAnswer(found=True, text=quote, passage=passage)


_TEXT = {
    "es": {"source": "Fuente: folleto oficial de Choices 2027-28, [página {page}]({url}).",
           "faq": "Fuente: [preguntas frecuentes de Choices]({url}).",
           "quote": "Lo más cercano que encontré en el folleto oficial (revise la página):",
           "not_found": "No encontré eso en el folleto oficial de Choices. Puede llamar al (213) 241-4177 o ver choices.lausd.net."},
    "en": {"source": "Source: official 2027-28 Choices brochure, [page {page}]({url}).",
           "faq": "Source: [Choices FAQ]({url}).",
           "quote": "The closest passage I found in the official brochure (check the page):",
           "not_found": "I couldn't find that in the official Choices brochure. You can call (213) 241-4177 or visit choices.lausd.net."},
}


def render_answer(result: BrochureAnswer, lang: str) -> str:
    t = _TEXT[lang if lang in _TEXT else "en"]
    if not result.found or result.passage is None:
        return t["not_found"]
    p = result.passage
    source = t["source"].format(page=p.page, url=p.source_url) if p.page else t["faq"].format(url=p.source_url)
    body = result.text if result.used_llm else f"{t['quote']}\n> {result.text}"
    return f"{body}\n\n{source}"
