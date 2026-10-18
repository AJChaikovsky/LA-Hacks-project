"""Every golden YAML passes locally with the LLM down; the checker catches bad replies."""
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from core import llm

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("replay_golden", ROOT / "scripts" / "replay_golden.py")
replay = importlib.util.module_from_spec(spec)
sys.modules["replay_golden"] = replay  # dataclasses need the module registered
spec.loader.exec_module(replay)

GOLDENS = sorted((ROOT / "tests" / "golden").glob("*.yaml"))


@pytest.fixture(autouse=True)
def llm_down(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda prompt, schema, fallback, **kw: fallback)


@pytest.mark.parametrize("engine", ["orchestrator", "harness"])
@pytest.mark.parametrize("path", GOLDENS, ids=lambda p: p.stem)
def test_golden_passes_locally(path, engine):
    """'orchestrator' is the code ASI:One runs (cards included); 'harness' is the chat_local reference."""
    golden = yaml.safe_load(path.read_text(encoding="utf-8"))
    results = replay.run_local(golden, engine)
    assert len(results) == 4
    assert [r.failures for r in results] == [[], [], [], []]


def test_checker_flags_missing_forbidden_wrong_intent_and_slow():
    golden = {"must_not_contain_any_turn": ["%"], "max_seconds_per_turn": 8}
    turn = {"must_contain": ["4 de 23 puntos"], "intent": "ask_odds"}
    failures = replay.check_turn(turn, golden, "Tiene 50% de posibilidades", "set_list", 9.0)
    assert len(failures) == 4


def test_p95():
    assert replay.p95([1.0]) == 1.0
    assert replay.p95([float(i) for i in range(1, 21)]) == pytest.approx(19.05)


# --- live mode over the hosted counselor's POST /chat (A5 host), with a fake endpoint ---

def test_live_mode_uses_chat_endpoint_and_checks_cards(monkeypatch):
    """Answer each POST /chat with the real handle_turn locally, as the hosted agent would."""
    from datetime import date as _date

    from agents.orchestrator import handle_turn
    from core.types import SessionState

    sessions, seen = {}, []

    def fake_post(session_id, text, timeout=30.0):
        seen.append(session_id)
        state = sessions.get(session_id, SessionState())
        state, parts, _ = handle_turn(state, text, _date(2026, 10, 17))
        sessions[session_id] = state
        return {"replies": [{"text": p.narration, "kind": p.kind, "payload": p.payload} for p in parts],
                "stage": state.stage}

    monkeypatch.setattr(replay, "post_chat", fake_post)
    golden = yaml.safe_load((ROOT / "tests" / "golden" / "maria_es.yaml").read_text(encoding="utf-8"))
    results = replay.run_live(golden)
    assert [r.failures for r in results] == [[], [], [], []]
    assert len(set(seen)) == 1 and replay.SESSION_RE.match(seen[0])


def test_live_mode_reports_unreachable_agent(monkeypatch):
    def down(session_id, text, timeout=30.0):
        raise ConnectionRefusedError("nothing on 127.0.0.1:8000")

    monkeypatch.setattr(replay, "post_chat", down)
    golden = yaml.safe_load((ROOT / "tests" / "golden" / "maria_en.yaml").read_text(encoding="utf-8"))
    results = replay.run_live(golden)
    assert all(any("ConnectionRefusedError" in f or "missing" in f for f in r.failures) for r in results)
