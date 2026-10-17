"""A2 handler tests: golden turns, reset, guards, bounds, splitting. Stubs only, no network.

Owner: Dev A, ticket A2 (#3).
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import date

# Orchestrator import needs a seed; never the production one here.
os.environ.setdefault("COUNSELOR_SEED", "a2-test-seed-not-production")

import pytest

from agents import orchestrator
from agents.orchestrator import bounded_turn, handle_turn, session_key, split_reply
from core.types import SessionState

TODAY = date(2026, 10, 17)

# Maria's 4 demo turns (same wording as scripts/chat_local.py GOLDEN).
GOLDEN = [
    "Hola, soy Maria. Vivo en Boyle Heights. Mi hijo Diego está en quinto grado y el próximo año entra a sexto. "
    "Tiene un IEP y le encanta el arte, dibuja todo el día. Yo trabajo en el centro y solo tengo mi celular. "
    "¿Qué escuelas me recomienda?",
    "¿Qué posibilidades tiene de entrar?",
    "Voy a poner las tres, y una más por si acaso: Markham, aunque queda lejísimos y no sé si lo aceptaría.",
    "Está bien, solo las tres. ¿Qué necesito para terminar?",
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for flag in ("INTENT_LLM", "EXTRACT_LLM", "EMAIL_LLM"):
        monkeypatch.setenv(flag, "off")


def test_golden_four_turns_walk_stages():
    state = SessionState()
    expected = [("profile_info", "matched"), ("ask_odds", "odds"), ("set_list", "listing"), ("ask_plan", "plan")]
    for text, (want_intent, want_stage) in zip(GOLDEN, expected):
        state, parts, intent = handle_turn(state, text, TODAY)
        assert intent == want_intent
        assert state.stage == want_stage
        assert parts and all(p.strip() for p in parts)


def test_reset_phrase_clears_state():
    state, _, _ = handle_turn(SessionState(), GOLDEN[0], TODAY)
    assert state.stage == "matched"
    state, parts, intent = handle_turn(state, "start over", TODAY)
    assert intent == "reset"
    assert state == SessionState()
    assert parts[0].strip()


def test_odds_before_matches_gets_scope_reply():
    new_state, parts, intent = handle_turn(SessionState(), GOLDEN[1], TODAY)
    assert intent == "ask_odds"
    assert new_state.stage == "intake"
    assert new_state.matches == []
    assert parts[0].strip()


def test_core_error_propagates_and_apology_has_hotline(monkeypatch):
    def boom(profile, codes):
        raise RuntimeError("points module fell over")

    monkeypatch.setattr(orchestrator.stubs, "estimate", boom)
    state, _, _ = handle_turn(SessionState(), GOLDEN[0], TODAY)
    with pytest.raises(RuntimeError):
        asyncio.run(bounded_turn(state, GOLDEN[1], TODAY))
    for lang in ("es", "en"):
        assert "(213) 241-4177" in orchestrator._SORRY[lang]


def test_slow_turn_times_out(monkeypatch):
    def slow(state, text, today=None):
        time.sleep(0.5)
        return state, ["late"], "help"

    monkeypatch.setattr(orchestrator, "handle_turn", slow)
    with pytest.raises(TimeoutError):
        asyncio.run(bounded_turn(SessionState(), "hola", TODAY, timeout=0.05))


def test_split_reply_two_parts_at_boundary():
    assert split_reply("hola") == ["hola"]
    long = ("x" * 3000) + "\n\n" + ("y" * 1500)
    parts = split_reply(long)
    assert parts == ["x" * 3000, "y" * 1500]
    assert all(len(p) <= 3500 for p in parts)


def test_session_state_round_trips_json():
    state, _, _ = handle_turn(SessionState(), GOLDEN[0], TODAY)
    restored = SessionState.model_validate_json(state.model_dump_json())
    assert restored == state


def test_sessions_are_independent_per_ctx_session():
    sender = "agent1qsender"
    k1 = session_key(sender, "11111111-aaaa")
    k2 = session_key(sender, "22222222-bbbb")
    assert k1 != k2
    assert session_key(sender, None) == f"session:{sender}"

    store: dict[str, str] = {}
    s1, _, _ = handle_turn(SessionState(), GOLDEN[0], TODAY)
    store[k1] = s1.model_dump_json()
    # The second conversation's key is untouched: it loads a fresh session.
    assert k2 not in store
    fresh = SessionState.model_validate_json(store[k2]) if k2 in store else SessionState()
    assert fresh == SessionState()
    assert SessionState.model_validate_json(store[k1]).stage == "matched"
