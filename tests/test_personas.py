"""C11 (#42): every persona is handled sensibly with the LLM down (the live rate is measured by the script)."""
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from core import llm

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("eval_personas", ROOT / "scripts" / "eval_personas.py")
ev = importlib.util.module_from_spec(spec)
sys.modules["eval_personas"] = ev
spec.loader.exec_module(ev)
PERSONAS = yaml.safe_load((ROOT / "tests" / "personas.yaml").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def llm_down(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda prompt, schema, fallback, **kw: fallback)


def test_persona_count():
    assert len(PERSONAS) == 32 and len({p["id"] for p in PERSONAS}) == 32


@pytest.mark.parametrize("persona", PERSONAS, ids=[p["id"] for p in PERSONAS])
def test_persona_handled_sensibly(persona):
    result = ev.evaluate(persona, ev._chat())
    assert result.passed, result.problems
