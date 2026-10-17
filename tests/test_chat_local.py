"""The local chat harness walks Maria's 4 golden turns with the LLM down."""
import importlib.util
from datetime import date
from pathlib import Path

import pytest

from core import llm
from core.types import SessionState

spec = importlib.util.spec_from_file_location("chat_local", Path(__file__).parent.parent / "scripts" / "chat_local.py")
chat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chat)


@pytest.fixture(autouse=True)
def llm_down(monkeypatch):
    monkeypatch.setattr(llm, "complete_json", lambda prompt, schema, fallback, **kw: fallback)


def test_golden_turns():
    state = SessionState()
    seen = []
    for text in chat.GOLDEN:
        state, reply, intent = chat.handle(state, text, today=date(2026, 10, 17))
        seen.append((intent, state.stage))
        assert reply.strip()
    assert seen == [("profile_info", "matched"), ("ask_odds", "odds"), ("set_list", "listing"), ("ask_plan", "plan")]
    assert state.profile.would_not_accept == ["1823703"]
    assert state.profile.intended_choices == ["1804703", "1817902", "1804702"]  # "solo las tres" trimmed Markham
    assert "6 de noviembre" in reply


def test_spanish_help_gets_spanish_reply():
    state, reply, intent = chat.handle(SessionState(), "Hola, necesito ayuda con Choices para mi hijo.")
    assert intent == "help" and state.stage == "intake" and not state.matches
    assert "Cuénteme" in reply


def test_missing_info_asks_instead_of_matching():
    state, reply, intent = chat.handle(SessionState(), "Mi hijo tiene un IEP.")
    assert intent == "profile_info" and state.stage == "intake" and not state.matches
    assert "grado" in reply


def test_reset_and_help():
    state, _, _ = chat.handle(SessionState(), chat.GOLDEN[0])
    state, reply, intent = chat.handle(state, "Quiero empezar de nuevo")
    assert intent == "reset" and state.stage == "intake"
