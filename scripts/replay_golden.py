"""Replay the golden conversations and report pass rate and latency.

Owner: Dev C, ticket C6 (#24).

    python scripts/replay_golden.py                      # both goldens, 1 run, local
    python scripts/replay_golden.py --runs 10            # the G4 check (must be 10/10)
    python scripts/replay_golden.py --no-llm             # same, with ASI:One "down"
    python scripts/replay_golden.py --file tests/golden/maria_es.yaml -v
    python scripts/replay_golden.py --live --runs 10     # against the running agent via scripts/ping.py

Local mode runs agents.orchestrator.handle_turn in-process: the same code ASI:One executes,
plus a format check of every Interactive Card it would send (--engine harness = chat_local). Live mode needs scripts/ping.py (Dev A, A5) to expose:

    send(text: str, session: str) -> str     # one ChatMessage to the counselor, returns the reply text

Exit code 0 only when every run of every golden passes.
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
GOLDEN_DIR = ROOT / "tests" / "golden"


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class TurnResult:
    say: str
    reply: str
    seconds: float
    failures: list[str] = field(default_factory=list)


def check_turn(spec: dict, golden: dict, reply: str, intent: str | None, seconds: float) -> list[str]:
    failures = []
    for s in spec.get("must_contain", []):
        if s not in reply:
            failures.append(f"missing {s!r}")
    from core.render import visible_text

    shown = visible_text(reply)  # forbidden strings are judged on what the parent reads, not on URLs
    for s in spec.get("must_not_contain", []) + golden.get("must_not_contain_any_turn", []):
        if s in shown:
            failures.append(f"forbidden {s!r}")
    if intent is not None and spec.get("intent") and intent != spec["intent"]:
        failures.append(f"intent {intent!r} != {spec['intent']!r}")
    limit = golden.get("max_seconds_per_turn")
    if limit and seconds > limit:
        failures.append(f"slow: {seconds:.1f}s > {limit}s")
    return failures


def engine_turn(engine: str):
    """A turn function (state, text, today) -> (state, reply text, intent, card problems).

    'orchestrator' runs agents.orchestrator.handle_turn, the exact code ASI:One executes, and checks
    every Interactive Card it would send (ASI:One silently drops invalid ones). 'harness' runs
    scripts/chat_local.handle (the C-lane reference)."""
    if engine == "harness":
        chat = _load_script("chat_local")
        return lambda state, text, today: (*chat.handle(state, text, today=today), [], [])

    from agents.cards import validate
    from agents.orchestrator import handle_turn

    def turn(state, text, today):
        state, parts, intent = handle_turn(state, text, today)
        reply = "\n\n".join(p.narration for p in parts)
        kinds = [p.kind for p in parts if p.kind]
        problems = [f"invalid {p.kind} card: {'; '.join(errs)}" for p in parts if p.kind
                    for errs in [validate(p.kind, p.payload or {})] if errs]
        return state, reply, intent, kinds, problems
    return turn


def run_local(golden: dict, engine: str = "orchestrator") -> list[TurnResult]:
    from core.types import SessionState

    turn = engine_turn(engine)
    today = golden.get("today")
    today = today if isinstance(today, date) else (date.fromisoformat(today) if today else None)
    state, results = SessionState(), []
    for spec in golden["turns"]:
        start = time.monotonic()
        try:
            state, reply, intent, kinds, problems = turn(state, spec["say"], today)
        except Exception as exc:  # noqa: BLE001 - a crash is a failed turn, not a crashed replay
            reply, intent, kinds, problems = f"[ERROR] {type(exc).__name__}: {exc}", None, [], []
        seconds = time.monotonic() - start
        failures = check_turn(spec, golden, reply, intent, seconds) + problems
        if engine == "orchestrator" and spec.get("card") and spec["card"] not in kinds:
            failures.append(f"expected a {spec['card']} card, got {kinds or 'none'}")
        results.append(TurnResult(spec["say"], reply, seconds, failures))
    return results


def run_live(golden: dict) -> list[TurnResult]:
    ping = _load_script("ping")
    if ping is None or not hasattr(ping, "send"):
        raise SystemExit("Live mode needs scripts/ping.py with send(text, session) -> str (ticket A5, #6).")
    session, results = f"golden-{uuid.uuid4().hex[:8]}", []
    for spec in golden["turns"]:
        start = time.monotonic()
        try:
            reply = ping.send(spec["say"], session=session)
        except Exception as exc:  # noqa: BLE001
            reply = f"[ERROR] {type(exc).__name__}: {exc}"
        seconds = time.monotonic() - start
        # The live agent doesn't report intents; content checks cover them.
        results.append(TurnResult(spec["say"], reply, seconds, check_turn(spec, golden, reply, None, seconds)))
    return results


def p95(values: list[float]) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def replay(files: list[Path], runs: int, live: bool, verbose: bool, engine: str = "orchestrator") -> bool:
    all_ok = True
    for path in files:
        golden = yaml.safe_load(path.read_text(encoding="utf-8"))
        passed, latencies = 0, []
        for run in range(1, runs + 1):
            results = run_live(golden) if live else run_local(golden, engine)
            latencies += [r.seconds for r in results]
            ok = not any(r.failures for r in results)
            passed += ok
            for i, r in enumerate(results, 1):
                if r.failures or verbose:
                    mark = "FAIL" if r.failures else "ok"
                    print(f"  [{golden['name']} run {run} turn {i}] {mark} {r.seconds:.1f}s {'; '.join(r.failures)}")
                    if verbose:
                        print("    " + r.reply.replace("\n", "\n    ")[:1500])
        all_ok &= passed == runs
        print(f"{golden['name']:10s} {'live' if live else engine:12s} {passed}/{runs} passed  "
              f"p50 {statistics.median(latencies):.1f}s  p95 {p95(latencies):.1f}s per turn")
    return all_ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", action="append", type=Path, help="golden YAML (default: all in tests/golden/)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--live", action="store_true", help="send to the running agent via scripts/ping.py")
    ap.add_argument("--no-llm", action="store_true", help="local only: disable the LLM")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every reply")
    ap.add_argument("--engine", choices=("orchestrator", "harness"), default="orchestrator",
                    help="local mode: the agent's handle_turn (default) or the chat_local harness")
    args = ap.parse_args()
    if args.no_llm:
        for key in ("EXTRACT_LLM", "INTENT_LLM", "EMAIL_LLM", "BROCHURE_LLM"):
            os.environ[key] = "off"
    logging.basicConfig(level=logging.WARNING)
    files = args.file or sorted(GOLDEN_DIR.glob("*.yaml"))
    return 0 if replay(files, args.runs, args.live, args.verbose, args.engine) else 1


if __name__ == "__main__":
    raise SystemExit(main())
