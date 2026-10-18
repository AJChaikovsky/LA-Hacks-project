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
from agents.orchestrator import (
    bounded_turn,
    guard_decision,
    handle_turn,
    session_key,
    split_reply,
    strip_mention,
)
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
        assert parts and all(p.narration.strip() for p in parts)


def test_reset_phrase_clears_state():
    state, _, _ = handle_turn(SessionState(), GOLDEN[0], TODAY)
    assert state.stage == "matched"
    state, parts, intent = handle_turn(state, "start over", TODAY)
    assert intent == "reset"
    assert state == SessionState()
    assert parts[0].narration.strip()


def test_odds_before_matches_gets_scope_reply():
    new_state, parts, intent = handle_turn(SessionState(), GOLDEN[1], TODAY)
    assert intent == "ask_odds"
    assert new_state.stage == "intake"
    assert new_state.matches == []
    assert parts[0].narration.strip()


def test_core_error_propagates_and_apology_has_hotline(monkeypatch):
    def boom(profile, codes):
        raise RuntimeError("points module fell over")

    # Demo path is real modules (USE_STUBS=false), so patch the real points.estimate.
    monkeypatch.setattr(orchestrator.points, "estimate", boom)
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


# --- A2 loop guard (count-based; no timing data was available in the log) ---------------


def test_loop_guard_caps_fast_pingpong():
    """(a) A 6-turn help/off_topic ping-pong is capped (default threshold 4 -> 3 replies, then silence)."""
    intents = ["help", "off_topic", "help", "off_topic", "help", "off_topic"]
    idle, sends = 0, 0
    for intent in intents:
        send, idle = guard_decision("intake", "intake", intent, idle)
        sends += int(send)
    assert sends == 3        # bot loop dies: 3 of 6 answered


def test_three_help_turns_all_answered_fourth_suppressed():
    """(b) A real person asking 3 help questions at intake gets 3 answers; the 4th no-progress
    turn is where the guard kicks in. Default threshold is 4."""
    idle, results = 0, []
    for _ in range(4):
        send, idle = guard_decision("intake", "intake", "help", idle)
        results.append(send)
    assert results == [True, True, True, False]


def test_intake_after_suppression_resets_counter():
    """(c) A real intake (stage advances intake->matched) is answered and resets the idle count."""
    idle = 5  # deep in a suppressed greeting loop
    send, idle = guard_decision("intake", "matched", "profile_info", idle)
    assert send is True
    assert idle == 0


def test_import_without_seed_does_not_exit(monkeypatch):
    """(d) Importing the module must never exit (issue #3); the seed is required only to run.

    Force the seed empty before reload. python-dotenv's load_dotenv(override=False) won't
    clobber an already-set value, so the empty string survives the module's load_dotenv() and
    build_agent() hits the check — proving it moved off the import path."""
    import importlib

    monkeypatch.setenv("COUNSELOR_SEED", "")
    mod = importlib.reload(orchestrator)  # must not raise SystemExit
    assert mod is not None
    with pytest.raises(SystemExit):
        mod.build_agent()  # the seed check now lives on the run path
    monkeypatch.setenv("COUNSELOR_SEED", "pytest-only-seed-never-registered")
    importlib.reload(orchestrator)  # restore a clean module for any later test


def test_strip_mention_shows_real_text():
    assert strip_mention("@agent1qw0e2abc hola Maria") == "hola Maria"
    assert strip_mention("no mention here") == "no mention here"


# --- End-to-end through the REAL handler (fake Context; no network, no teammate) ---------

import logging

from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent


class _FakeStorage:
    def __init__(self):
        self._d: dict = {}

    def get(self, k):
        return self._d.get(k)

    def has(self, k):
        return k in self._d

    def set(self, k, v):
        self._d[k] = v

    def remove(self, k):
        self._d.pop(k, None)


class _FakeContext:
    """Just enough of uagents.Context for on_chat_message: session, storage, logger, async send."""

    def __init__(self, session="sess-e2e-0001"):
        self.session = session
        self.storage = _FakeStorage()
        self.logger = logging.getLogger("test.orchestrator")
        self.sent: list = []

    async def send(self, destination, message):
        self.sent.append(message)


def _msg(text: str) -> ChatMessage:
    return ChatMessage(content=[TextContent(text=text)])


def _counts(ctx: _FakeContext) -> tuple[int, int]:
    acks = sum(isinstance(m, ChatAcknowledgement) for m in ctx.sent)
    replies = sum(isinstance(m, ChatMessage) for m in ctx.sent)
    return acks, replies


def test_handler_loop_guard_end_to_end():
    """6 greeting turns in one session -> 6 acks, 3 replies (default threshold 4); intake after -> replied."""
    sender = "agent1qfakesender"
    ctx = _FakeContext()
    for _ in range(6):
        asyncio.run(orchestrator.on_chat_message(ctx, sender, _msg("¿Qué puedes hacer?")))
    acks, replies = _counts(ctx)
    assert acks == 6          # ack ALWAYS sent, even when the reply is suppressed
    assert replies == 3       # loop guard caps the greeting storm at 3

    before = len(ctx.sent)
    asyncio.run(orchestrator.on_chat_message(ctx, sender, _msg(GOLDEN[0])))  # real intake
    new = ctx.sent[before:]
    assert any(isinstance(m, ChatAcknowledgement) for m in new)
    assert any(isinstance(m, ChatMessage) for m in new)  # intake answered, counter reset
