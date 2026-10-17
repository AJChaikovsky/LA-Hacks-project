"""Classify a parent's message into an Intent: card JSON, keyword rules, stage default, LLM fallback.

Owner: Dev C, ticket C2 (#20).

    classify(text, stage) -> Intent                     (frozen signature, see core/stubs.py)
    parse_card_selection(text) -> dict | None           what a card click selected
    scope_reply(stage, language) -> str                 one polite off-topic line + next step

Order (first hit wins):
  1. card click: a JSON object (direct @mention) or a program code / card button label (planner prose)
  2. reset, then keyword rules: ask_odds > ask_plan > set_list  ("solo las tres. ¿Qué necesito?" = ask_plan)
  3. profile facts found by core.extract.prepass -> profile_info
  4. help keywords
  5. short affirmation ("sí", "ok") -> the stage's natural next step
  6. LLM (asi1-mini) -> else intake: profile_info, later stages: help
"""
from __future__ import annotations

import json
import logging
import os
import re

from pydantic import BaseModel

from core import llm
from core.extract import norm, prepass
from core.types import Intent, Stage

log = logging.getLogger("intent")

_PROGRAM_CODE = re.compile(r"\b1\d{6}(?:TWS|TWM)?\b")
_CARD_LABELS = re.compile(
    r"^(ver detalles|agregar a mi lista|aprobar|approve|view details|add to my list|select|elegir|"
    r"seleccione|seleccionar|i selected|selected)\b"
)
_RESET = re.compile(r"empezar de nuevo|comenzar de nuevo|volver a empezar|start over|\breset\b|reiniciar|borrar todo")
_ODDS = re.compile(
    r"posibilidad|probabilidad|\bchances?\b|\bodds\b|oportunidad de entrar|cuantos puntos|\bpuntos\b|\bpoints\b|"
    r"\blikely\b|get in\b|que tan dificil|how hard|competitiv|competid|lo van a aceptar|will (he|she) get"
)
_PLAN = re.compile(
    r"que necesito|que tengo que hacer|que hago ahora|que documentos|\bdocumentos\b|checklist|\bpasos\b|"
    r"\bsteps\b|como (aplico|lo hago|me inscribo|termino)|how do i (apply|finish)|what do i need|next steps|"
    r"\bterminar\b|\bfinish\b|fecha limite|deadline|\bcorreo\b|\bemail\b|coordinador|coordinator|"
    r"what('?s| is) next|y ahora que"
)
_LIST = re.compile(
    r"voy a poner|voy a listar|voy a elegir|\bpongo\b|poner (las|los|a|en)|i'?ll (list|put|pick)|"
    r"i (will|am going to|'?m going to) (list|put|pick)|\bmy list\b|\bmi lista\b|agregar|\badd\b|"
    r"primera opcion|first choice|segunda opcion|second choice|\brank\b|ordenar|en ese orden|in that order|"
    r"\b(las|los|all|the) (tres|3|three)\b"
)
_HELP = re.compile(
    r"\bayuda\b|\bhelp\b|que puedes hacer|que haces|what can you do|como funciona|how does (this|it) work|"
    r"quien eres|who are you"
)
_AFFIRM = re.compile(
    r"^(si|ok|okay|dale|claro|yes|sure|yep|yeah|va|perfecto|por favor|please|de acuerdo|esta bien|bueno|"
    r"si por favor|yes please|go ahead|adelante)[\s.!]*$"
)
_PROFILE_KEYS = {"current_grade", "applying_grade", "interests", "iep", "home_neighborhood", "parent_work_area",
                 "child_name", "english_learner", "gifted", "in_magnet_now", "waitlist_years", "home_language"}

# What a bare "yes" means after the agent's closing question at each stage.
_AFFIRM_NEXT: dict[str, Intent] = {
    "intake": "help",
    "matched": "ask_odds",   # "¿Quiere saber qué posibilidades tiene?"
    "odds": "set_list",      # "¿Va a poner estas tres?" -> A3 treats an empty list as the 3 matches
    "listing": "ask_plan",   # "¿Preparo su lista de documentos y pasos?"
    "plan": "help",
}


def _label_actions() -> dict[str, str]:
    """Card button labels (es + en, from i18n) -> the action their selection carries."""
    from core.render import strings

    keys = {"details": "details", "add": "add", "back": "back", "approve": "approve_packet", "reject": "reject_packet"}
    return {norm(strings(lang)["cards"][k]): action for lang in ("es", "en") for k, action in keys.items()}


def parse_card_selection(text: str) -> dict | None:
    """The selection from an Interactive Card click, or None if this isn't one.

    Direct @mention clicks arrive as the selection JSON; planner-routed clicks arrive as
    prose that names the button (and usually the school), e.g. "Ver detalles: Belvedere".
    """
    from core.extract import find_school_mentions

    raw = (text or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data
    codes = _PROGRAM_CODE.findall(raw)
    if codes and len(raw.split()) <= 12:
        return {"code": codes[0], "raw_text": raw}
    t = norm(raw)
    for label, action in _label_actions().items():
        if label in t:
            selection = {"action": action, "raw_text": raw}
            mentions = find_school_mentions(t)
            if mentions:
                selection["code"] = mentions[0][1]
            return selection
    if _CARD_LABELS.search(t):
        return {"raw_text": raw}
    return None


class IntentChoice(BaseModel):
    intent: Intent


_SYSTEM = (
    "Classify a parent's latest message in a conversation with an LAUSD school-choice counselor. "
    "Reply with one intent: profile_info (describes the child or family), ask_odds (chances of getting in, "
    "points), set_list (which schools they will put on the application), ask_plan (documents, steps, "
    "deadlines, finishing the application, email to the school), card_selection, reset (start over), "
    "help (what the counselor can do), off_topic (anything unrelated to school choice). "
    "The conversation stage is given for context."
)


def _llm_classify(text: str, stage: Stage) -> Intent | None:
    if os.getenv("INTENT_LLM", "on").lower() == "off":
        return None
    sentinel = IntentChoice(intent="help")
    result = llm.complete_json(
        f"Stage: {stage}\nMessage: {text}", IntentChoice, sentinel, system=_SYSTEM,
        model=os.getenv("INTENT_MODEL", "asi1-mini"),
    )
    return None if result is sentinel else result.intent


def classify(text: str, stage: Stage) -> Intent:
    t = norm(text).strip()
    if parse_card_selection(text) is not None:
        return _log("card_selection", "card", text)
    if _RESET.search(t):
        return _log("reset", "keyword", text)
    if _ODDS.search(t):
        return _log("ask_odds", "keyword", text)
    if _PLAN.search(t):
        return _log("ask_plan", "keyword", text)
    if _LIST.search(t):
        return _log("set_list", "keyword", text)
    if _PROFILE_KEYS & prepass(text).keys():
        return _log("profile_info", "facts", text)
    if _HELP.search(t):
        return _log("help", "keyword", text)
    if _AFFIRM.match(re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", t)).strip()):
        return _log(_AFFIRM_NEXT[stage], "affirm", text)
    guess = _llm_classify(text, stage)
    if guess:
        return _log(guess, "llm", text)
    return _log("profile_info" if stage == "intake" else "help", "default", text)


def _log(intent: Intent, rule: str, text: str) -> Intent:
    log.info("[INTENT] %s via %s: %r", intent, rule, (text or "")[:60])
    return intent


_SCOPE = {
    "es": "Solo puedo ayudarle con la solicitud de escuelas Choices de LAUSD.",
    "en": "I can only help with the LAUSD Choices school application.",
}
_NEXT_STEP = {
    "es": {
        "intake": "Cuénteme de su hijo o hija: grado, intereses, si tiene IEP y en qué barrio viven.",
        "matched": "¿Quiere saber qué posibilidades tiene en estas escuelas?",
        "odds": "¿Cuáles escuelas va a poner en su solicitud?",
        "listing": "¿Preparo su lista de documentos, fechas y pasos?",
        "plan": "¿Tiene otra pregunta sobre su solicitud?",
    },
    "en": {
        "intake": "Tell me about your child: grade, interests, whether they have an IEP, and your neighborhood.",
        "matched": "Would you like to know your child's chances at these schools?",
        "odds": "Which schools will you put on your application?",
        "listing": "Shall I prepare your checklist, deadlines, and steps?",
        "plan": "Do you have another question about your application?",
    },
}


def scope_reply(stage: Stage, language: str) -> str:
    """One polite sentence about scope, then the next step for this stage."""
    lang = language if language in _SCOPE else "en"
    return f"{_SCOPE[lang]} {_NEXT_STEP[lang][stage]}"
