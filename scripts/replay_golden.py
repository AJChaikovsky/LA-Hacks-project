"""Replay the golden conversations and report pass rate and latency.

Owner: Dev C, ticket C6 (#24).

    python scripts/replay_golden.py                      # both goldens, 1 run, local
    python scripts/replay_golden.py --runs 10            # the G4 check (must be 10/10)
    python scripts/replay_golden.py --no-llm             # same, with ASI:One "down"
    python scripts/replay_golden.py --file tests/golden/maria_es.yaml -v
    python scripts/replay_golden.py --live --runs 10     # against the running agent via scripts/ping.py

Local mode runs scripts/chat_local.handle() in-process (real C modules; Dev B parts are
real once merged, stubs until then). Live mode needs scripts/ping.py (Dev A, A5) to expose:

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
    for s in spec.get("must_not_contain", []) + golden.get("must_not_contain_any_turn", []):
        if s in reply:
            failures.append(f"forbidden {s!r}")
    if intent is not None and spec.get("intent") and intent != spec["intent"]:
        failures.append(f"intent {intent!r} != {spec['intent']!r}")
    limit = golden.get("max_seconds_per_turn")
    if limit and seconds > limit:
        failures.append(f"slow: {seconds:.1f}s > {limit}s")
    return failures


def run_local(golden: dict) -> list[TurnResult]:
    chat = _load_script("chat_local")
    from core.types import SessionState

    today = golden.get("today")
    today = today if isinstance(today, date) else (date.fromisoformat(today) if today else None)
    state, results = SessionState(), []
    for spec in golden["turns"]:
        start = time.monotonic()
        try:
            state, reply, intent = chat.handle(state, spec["say"], today=today)
        except Exception as exc:  # noqa: BLE001 - a crash is a failed turn, not a crashed replay
            reply, intent = f"[ERROR] {type(exc).__name__}: {exc}", None
        seconds = time.monotonic() - start
        results.append(TurnResult(spec["say"], reply, seconds, check_turn(spec, golden, reply, intent, seconds)))
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


def replay(files: list[Path], runs: int, live: bool, verbose: bool) -> bool:
    all_ok = True
    for path in files:
        golden = yaml.safe_load(path.read_text(encoding="utf-8"))
        passed, latencies = 0, []
        for run in range(1, runs + 1):
            results = (run_live if live else run_local)(golden)
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
        print(f"{golden['name']:10s} {'live' if live else 'local'}  {passed}/{runs} passed  "
              f"p50 {statistics.median(latencies):.1f}s  p95 {p95(latencies):.1f}s per turn")
    return all_ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", action="append", type=Path, help="golden YAML (default: all in tests/golden/)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--live", action="store_true", help="send to the running agent via scripts/ping.py")
    ap.add_argument("--no-llm", action="store_true", help="local only: disable the LLM")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every reply")
    args = ap.parse_args()
    if args.no_llm:
        os.environ["EXTRACT_LLM"] = os.environ["INTENT_LLM"] = os.environ["EMAIL_LLM"] = "off"
    logging.basicConfig(level=logging.WARNING)
    files = args.file or sorted(GOLDEN_DIR.glob("*.yaml"))
    return 0 if replay(files, args.runs, args.live, args.verbose) else 1


if __name__ == "__main__":
    raise SystemExit(main())
