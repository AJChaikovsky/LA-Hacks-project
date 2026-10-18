"""G2 gate: drive the ORCHESTRATOR's handle_turn through the golden YAMLs.

Owner: Dev A, ticket A3 (#4).

scripts/replay_golden.py checks the reference scripts/chat_local.handle(). This checks the
agent's own path (agents.orchestrator.handle_turn) against the same goldens with the same
check_turn, so the live agent — not just the reference — is golden-gated. Runs offline with
real core modules (USE_STUBS=false, LLM off) for determinism.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date
from pathlib import Path

# Orchestrator import needs a seed; never the production one here.
os.environ.setdefault("COUNSELOR_SEED", "pytest-only-seed-never-registered")

import pytest
import yaml

from agents import orchestrator
from core.types import SessionState

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "tests" / "golden"


def _load_replay():
    path = ROOT / "scripts" / "replay_golden.py"
    spec = importlib.util.spec_from_file_location("replay_golden", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass resolution needs the module registered before exec
    spec.loader.exec_module(mod)
    return mod


replay = _load_replay()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for flag in ("INTENT_LLM", "EXTRACT_LLM", "EMAIL_LLM"):
        monkeypatch.setenv(flag, "off")
    monkeypatch.setenv("USE_STUBS", "false")  # force the real modules, not the Maria fixtures


@pytest.mark.parametrize("name", ["maria_es", "maria_en"])
def test_orchestrator_passes_golden(name):
    path = GOLDEN_DIR / f"{name}.yaml"
    if not path.exists():
        pytest.skip(f"{name}.yaml not present")
    golden = yaml.safe_load(path.read_text(encoding="utf-8"))
    today = golden.get("today")
    today = today if isinstance(today, date) else (date.fromisoformat(str(today)) if today else None)

    state, failures = SessionState(), []
    for spec in golden["turns"]:
        state, parts, intent = orchestrator.handle_turn(state, spec["say"], today)
        reply = "\n\n".join(p.narration for p in parts)
        for f in replay.check_turn(spec, golden, reply, intent, 0.0):
            failures.append(f"[{name} turn {spec['say'][:24]!r}] {f}")
    assert not failures, "\n".join(failures)
