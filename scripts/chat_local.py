"""Chat with the counselor in your terminal, no Agentverse or ASI:One needed.

Uses the real language modules (core/extract, core/intent, core/render) and, for Dev B's
modules, the real function when it exists or the fixture stub until it does. The startup
banner says which is which.

    python scripts/chat_local.py              # interactive
    python scripts/chat_local.py --golden     # replay Maria's 4 demo turns
    python scripts/chat_local.py --no-llm     # pre-pass/keyword rules only (LLM down)

Commands while chatting: /state (show the session), /reset, /quit.

The handle() function is a reference for the orchestrator handlers (A2/A3); it is not the agent.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import stubs  # noqa: E402
from core.types import SessionState  # noqa: E402

GOLDEN = [
    "Hola, soy Maria. Vivo en Boyle Heights. Mi hijo Diego está en quinto grado y el próximo año entra a sexto. "
    "Tiene un IEP y le encanta el arte, dibuja todo el día. Yo trabajo en el centro y solo tengo mi celular. "
    "¿Qué escuelas me recomienda?",
    "¿Qué posibilidades tiene de entrar?",
    "Voy a poner las tres, y una más por si acaso: Markham, aunque queda lejísimos y no sé si lo aceptaría.",
    "Está bien, solo las tres. ¿Qué necesito para terminar?",
]

# (module, function) for each step; falls back to core.stubs when the real one isn't written yet.
PARTS = {
    "extract": ("core.extract", "extract"),
    "classify": ("core.intent", "classify"),
    "match": ("core.matcher", "match"),
    "estimate": ("core.points", "estimate"),
    "check_list": ("core.guardrails", "check_list"),
    "build_plan": ("core.paperwork", "build_plan"),
    "render_matches": ("core.render", "render_matches"),
    "render_points": ("core.render", "render_points"),
    "render_warnings": ("core.render", "render_warnings"),
    "render_plan": ("core.render", "render_plan"),
}


def load_parts() -> tuple[dict, dict]:
    fns, source = {}, {}
    for name, (module, attr) in PARTS.items():
        fn = getattr(importlib.import_module(module), attr, None)
        fns[name], source[name] = (fn, "real") if callable(fn) else (getattr(stubs, attr), "STUB")
    return fns, source


FN, SOURCE = load_parts()

_MISSING = {
    "es": "Para buscar escuelas necesito saber: {fields}. ¿Me cuenta?",
    "en": "To find schools I need to know: {fields}. Can you tell me?",
}
_FIELD_NAMES = {
    "es": {"grade": "en qué grado está", "where": "en qué barrio viven o qué le gusta"},
    "en": {"grade": "what grade your child is in", "where": "your neighborhood or what they enjoy"},
}
_HELP = {
    "es": "Le ayudo con la solicitud Choices de LAUSD: busco 3 escuelas para su hijo o hija, calculo sus puntos, "
          "reviso su lista y le preparo documentos, fechas y un correo. Cuénteme de su hijo o hija para empezar.",
    "en": "I help with the LAUSD Choices application: I find 3 schools for your child, estimate points, check your "
          "list, and prepare documents, dates, and an email. Tell me about your child to start.",
}


def handle(state: SessionState, text: str, today: date | None = None) -> tuple[SessionState, str, str]:
    """One turn: returns (new state, reply markdown, intent)."""
    from core.extract import detect_language, parse_school_list
    from core.intent import parse_card_selection, scope_reply
    from core.render import render_ask_grade

    today = today or date.today()
    intent = FN["classify"](text, state.stage)
    # Reply in the language of the latest message, on every turn.
    p = state.profile.model_copy(update={"language": detect_language(text) or state.profile.language})
    state = state.model_copy(update={"profile": p})
    lang = p.language

    if intent == "reset":
        return SessionState(), _HELP[lang], intent

    if intent == "profile_info":
        p = FN["extract"](text, p)
        state = state.model_copy(update={"profile": p})
        lang = p.language
        if not p.applying_grade:  # never assume middle school: ask
            return state, render_ask_grade(p), intent
        if not (p.interests or p.home_neighborhood):
            return state, _MISSING[lang].format(fields=_FIELD_NAMES[lang]["where"]), intent
        matches = FN["match"](p)
        # Zero matches: stay at intake so the parent can correct the grade; no odds offer.
        state = state.model_copy(update={"matches": matches, "stage": "matched" if matches else "intake"})
        return state, FN["render_matches"](matches, p), intent

    if intent in ("ask_odds", "set_list", "ask_plan") and not state.matches:
        return state, scope_reply("intake", lang), intent

    codes = p.intended_choices or [m.code for m in state.matches]

    if intent == "ask_odds":
        estimates = FN["estimate"](p, codes)
        state = state.model_copy(update={"estimates": estimates, "stage": "odds"})
        return state, FN["render_points"](estimates, p), intent

    if intent == "set_list":
        p = FN["extract"](text, p)  # picks up doubts like "no sé si lo aceptaría"
        choices, doubtful = parse_school_list(text, [m.code for m in state.matches])
        choices = choices or [m.code for m in state.matches]
        p = p.model_copy(update={
            "intended_choices": choices,
            "would_not_accept": list(dict.fromkeys(p.would_not_accept + doubtful)),
        })
        warnings = FN["check_list"](p, choices)
        state = state.model_copy(update={"profile": p, "warnings": warnings, "stage": "listing"})
        return state, FN["render_warnings"](warnings, p), intent

    if intent == "ask_plan":
        choices, _ = parse_school_list(text, [m.code for m in state.matches])
        if choices:  # "solo las tres" trims the list before planning
            p = p.model_copy(update={"intended_choices": choices})
        plan = FN["build_plan"](p, p.intended_choices or codes, lang, today)
        state = state.model_copy(update={"profile": p, "plan": plan, "stage": "plan"})
        return state, FN["render_plan"](plan, p), intent

    if intent == "card_selection":
        sel = parse_card_selection(text) or {}
        m = next((m for m in state.matches if m.code == sel.get("code")), None)
        if m:
            detail = f"**{m.name} · {m.theme}**\n- {m.address or ''}\n- {m.phone or ''}"
            return state, detail, intent
        return state, scope_reply(state.stage, lang), intent

    if intent == "help":
        return state, _HELP[lang], intent

    return state, scope_reply(state.stage, lang), intent


def banner() -> str:
    rows = [f"  {name:16s} {SOURCE[name]}" for name in PARTS]
    llm = "off" if os.getenv("EXTRACT_LLM") == "off" else ("on" if os.getenv("ASI1_API_KEY") else "off (no key)")
    return "Modules (real = your code, STUB = fixture until Dev B merges):\n" + "\n".join(rows) + f"\n  LLM: {llm}\n"


def run_turn(state: SessionState, text: str) -> SessionState:
    start = time.monotonic()
    try:
        state, reply, intent = handle(state, text)
    except Exception as exc:  # noqa: BLE001 - show the bug, keep chatting
        logging.exception("turn failed")
        reply, intent = f"[ERROR] {type(exc).__name__}: {exc}", "error"
    print(f"\n\033[2m[intent={intent} stage={state.stage} {time.monotonic() - start:.1f}s]\033[0m")
    print(reply + "\n")
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--golden", action="store_true", help="replay Maria's 4 demo turns and exit")
    ap.add_argument("--no-llm", action="store_true", help="disable the LLM (keyword rules only)")
    ap.add_argument("--debug", action="store_true", help="show [EXTRACT]/[INTENT]/[LLM] logs")
    args = ap.parse_args()
    if args.no_llm:
        os.environ["EXTRACT_LLM"] = os.environ["INTENT_LLM"] = os.environ["EMAIL_LLM"] = "off"
    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING, format="\033[2m%(name)s %(message)s\033[0m")
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    print(banner())
    state = SessionState()
    if args.golden:
        for text in GOLDEN:
            print(f"\033[1mMaria:\033[0m {text}")
            state = run_turn(state, text)
        return 0

    print("Escriba como Maria (o en inglés). /state, /reset, /quit\n")
    while True:
        try:
            text = input("\033[1mUsted:\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text == "/quit":
            return 0
        if text == "/reset":
            state = SessionState()
            print("(reset)\n")
            continue
        if text == "/state":
            print(state.model_dump_json(indent=2, exclude={"plan"}) + "\n")
            continue
        state = run_turn(state, text)


if __name__ == "__main__":
    raise SystemExit(main())
