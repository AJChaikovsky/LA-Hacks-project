"""Run the 30 parent personas through the real modules and report how many are handled sensibly.

Owner: Dev C, ticket C11 (#42).

    python scripts/eval_personas.py              # LLM on (needs ASI1_API_KEY)
    python scripts/eval_personas.py --no-llm     # LLM down
    python scripts/eval_personas.py --min 0.9    # exit 1 below this rate

A persona passes when the outcome is one of its `expect` values AND every safety check holds:
no crash, reply in the expected language, no percentages, no odds offer with zero matches, and
every matched program serves the child's applying grade.
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PERSONAS = ROOT / "tests" / "personas.yaml"
TODAY = date(2026, 10, 17)

_MARKERS = {  # substrings that identify each outcome, es and en
    "ask_grade": ("¿En qué grado está", "What grade is"),
    "no_programs": ("choices.lausd.net o llamar", "choices.lausd.net or call"),
    "scope": ("Solo puedo ayudarle", "I can only help"),
    "help": ("Le ayudo con la solicitud", "I help with the LAUSD Choices"),
    "rules": ("Fuente: folleto oficial", "Source: official", "Fuente: [preguntas frecuentes", "Source: [Choices FAQ"),
}


def _chat():
    spec = importlib.util.spec_from_file_location("chat_local", ROOT / "scripts" / "chat_local.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["chat_local"] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class Result:
    id: str
    outcome: str
    passed: bool
    problems: list[str] = field(default_factory=list)


def outcome_of(state, reply: str) -> str:
    if state.matches:
        return "matches"
    for name, markers in _MARKERS.items():
        if any(m in reply for m in markers):
            return name
    return "other"


def turn_function(engine: str = "orchestrator"):
    """(state, text, today) -> (state, reply, card problems) for the agent's handle_turn or the harness."""
    if engine == "harness":
        chat = _chat()
        return lambda state, text, today: (*chat.handle(state, text, today=today)[:2], [])

    from agents.cards import validate
    from agents.orchestrator import handle_turn

    def turn(state, text, today):
        state, parts, _ = handle_turn(state, text, today)
        problems = [f"invalid {p.kind} card" for p in parts if p.kind and validate(p.kind, p.payload or {})]
        return state, "\n\n".join(p.narration for p in parts), problems
    return turn


def evaluate(persona: dict, turn) -> Result:
    from core.render import schools, visible_text
    from core.types import SessionState

    try:
        state, reply, card_problems = turn(SessionState(), persona["say"], TODAY)
    except Exception as exc:  # noqa: BLE001
        return Result(persona["id"], "crash", False, [f"{type(exc).__name__}: {exc}"])
    outcome = outcome_of(state, reply)
    problems = list(card_problems)
    if outcome not in persona["expect"]:
        problems.append(f"outcome {outcome!r} not in {persona['expect']}")
    if persona.get("lang") and state.profile.language != persona["lang"]:
        problems.append(f"language {state.profile.language!r} != {persona['lang']!r}")
    if "%" in visible_text(reply):
        problems.append("shows a percentage")
    if not state.matches and any(w in reply for w in ("posibilidades tiene", "chances of getting in")):
        problems.append("offers odds with zero matches")
    grade = state.profile.applying_grade
    for m in state.matches:
        grades = schools().get(m.code, {}).get("grades", [])
        if grade is not None and grades and grade not in grades:
            problems.append(f"{m.code} doesn't serve grade {grade}")
    return Result(persona["id"], outcome, not problems, problems)


def run(no_llm: bool, engine: str = "orchestrator") -> list[Result]:
    if no_llm:
        for key in ("EXTRACT_LLM", "INTENT_LLM", "EMAIL_LLM", "BROCHURE_LLM"):
            os.environ[key] = "off"
    turn = turn_function(engine)
    personas = yaml.safe_load(PERSONAS.read_text(encoding="utf-8"))
    return [evaluate(p, turn) for p in personas]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--min", type=float, default=0.0, help="exit 1 when the pass rate is below this")
    ap.add_argument("--engine", choices=("orchestrator", "harness"), default="orchestrator")
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)
    results = run(args.no_llm, args.engine)
    for r in results:
        if not r.passed:
            print(f"FAIL {r.id:20s} {r.outcome:12s} {'; '.join(r.problems)}")
    rate = sum(r.passed for r in results) / len(results)
    print(f"{sum(r.passed for r in results)}/{len(results)} personas handled sensibly ({rate:.0%}) "
          f"· LLM {'off' if args.no_llm else 'on'} · {args.engine}")
    return 0 if rate >= args.min else 1


if __name__ == "__main__":
    raise SystemExit(main())
