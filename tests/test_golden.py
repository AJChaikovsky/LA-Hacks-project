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


@pytest.mark.parametrize("path", GOLDENS, ids=lambda p: p.stem)
def test_golden_passes_locally(path):
    golden = yaml.safe_load(path.read_text(encoding="utf-8"))
    results = replay.run_local(golden)
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
