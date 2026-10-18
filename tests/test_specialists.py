"""A4: the orchestrator↔specialist bridge and the "Consulté" footer. Offline, no network.

Owner: Dev A, ticket A4 (#5).

The bridge runs send_and_receive from handle_turn's worker thread via run_coroutine_threadsafe.
These tests drive it with a fake async send_and_receive: fast reply, slow (timeout), and raise.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date

# Orchestrator import needs a seed; never the production one here.
os.environ.setdefault("COUNSELOR_SEED", "pytest-only-seed-never-registered")

import pytest

from agents import orchestrator
from agents.messages import PointsResponse
from agents.orchestrator import RemoteTools, bounded_turn, handle_turn, on_chat_message
from core import points as core_points
from core.types import FamilyProfile, SessionState

TODAY = date(2026, 10, 17)
GOLDEN0 = (
    "Hola, soy Maria. Vivo en Boyle Heights. Mi hijo Diego está en quinto grado y el próximo año entra a sexto. "
    "Tiene un IEP y le encanta el arte, dibuja todo el día. Yo trabajo en el centro y solo tengo mi celular. "
    "¿Qué escuelas me recomienda?"
)
ODDS = "¿Qué posibilidades tiene de entrar?"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for flag in ("INTENT_LLM", "EXTRACT_LLM", "EMAIL_LLM"):
        monkeypatch.setenv(flag, "off")
    monkeypatch.setenv("USE_STUBS", "false")


def _matched_state() -> SessionState:
    state, _, _ = handle_turn(SessionState(), GOLDEN0, TODAY)  # local → reaches "matched"
    assert state.stage == "matched"
    return state


def _points_response(profile: FamilyProfile, codes: list[str]) -> PointsResponse:
    est = core_points.estimate(profile, codes)
    return PointsResponse(
        session_id="x",
        estimates_json=json.dumps([e.model_dump(mode="json") for e in est], ensure_ascii=False),
        warnings_json="[]",
    )


class _FakeCtx:
    """Just enough of Context for the bridge: an async send_and_receive + a logger."""

    def __init__(self, mode: str):
        self.mode = mode
        self.logger = logging.getLogger("test.remote")
        self.session = "sess-remote-1"

    async def send_and_receive(self, destination, message, response_type=None, sync=False, timeout=None):
        if self.mode == "fast":
            profile = FamilyProfile.model_validate_json(message.profile_json)
            return _points_response(profile, message.codes), object()
        if self.mode == "slow":
            await asyncio.sleep((timeout or 1) + 0.3)  # mimic send_and_receive timing out
            return None, object()
        raise RuntimeError("boom")  # mode == "raise"


def _remote(ctx, timeout=1) -> RemoteTools:
    return RemoteTools(
        ctx=ctx, loop=asyncio.get_running_loop(), session_id="s",
        points_addr="agent1qpoints", paperwork_addr="agent1qpw", timeout=timeout,
    )


def test_bridge_fast_uses_remote():
    state = _matched_state()

    async def run():
        remote = _remote(_FakeCtx("fast"))
        res = await bounded_turn(state, ODDS, TODAY, remote=remote)
        return res, remote

    (new_state, parts, intent), remote = asyncio.run(run())
    assert intent == "ask_odds"
    assert remote.consulted == {"points"}      # remote reply was used
    assert parts and parts[0].narration.strip()


def test_bridge_slow_falls_back_under_budget():
    state = _matched_state()

    async def run():
        remote = _remote(_FakeCtx("slow"), timeout=1)
        t0 = time.monotonic()
        res = await bounded_turn(state, ODDS, TODAY, remote=remote)
        return res, remote, time.monotonic() - t0

    (new_state, parts, intent), remote, elapsed = asyncio.run(run())
    assert remote.consulted == set()           # fallback → not consulted → no footer
    assert parts and "puntos" in parts[0].narration  # correct local answer rendered
    assert elapsed < 8


def test_bridge_raise_falls_back():
    state = _matched_state()

    async def run():
        remote = _remote(_FakeCtx("raise"))
        res = await bounded_turn(state, ODDS, TODAY, remote=remote)
        return res, remote

    (new_state, parts, intent), remote = asyncio.run(run())
    assert remote.consulted == set()
    assert parts and "puntos" in parts[0].narration


# --- Footer at the on_chat_message layer ------------------------------------

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


class _FakeAgentCtx:
    """Context for on_chat_message: storage, logger, session, recording send, points-only remote."""

    def __init__(self):
        self.storage = _FakeStorage()
        self.logger = logging.getLogger("test.agent")
        self.session = "sess-footer-1"
        self.sent: list = []

    async def send(self, destination, message):
        self.sent.append(message)

    async def send_and_receive(self, destination, message, response_type=None, sync=False, timeout=None):
        profile = FamilyProfile.model_validate_json(message.profile_json)
        return _points_response(profile, message.codes), object()


def _msg(text: str) -> ChatMessage:
    return ChatMessage(content=[TextContent(text=text)])


def test_footer_lists_only_points_on_a_points_turn(monkeypatch):
    monkeypatch.setenv("USE_REMOTE_AGENTS", "true")
    monkeypatch.setenv("POINTS_AGENT_ADDRESS", "agent1qpoints")
    monkeypatch.setenv("PAPERWORK_AGENT_ADDRESS", "agent1qpaperwork")
    ctx = _FakeAgentCtx()
    asyncio.run(on_chat_message(ctx, "agent1qsender", _msg(GOLDEN0)))  # intake → matched (no specialist)
    before = len(ctx.sent)
    asyncio.run(on_chat_message(ctx, "agent1qsender", _msg(ODDS)))     # ask_odds → consults points only
    replies = [m for m in ctx.sent[before:] if isinstance(m, ChatMessage)]
    text = "\n".join(c.text for m in replies for c in m.content if isinstance(c, TextContent))
    assert "Consulté: Agente de Puntos" in text
    assert "Trámites" not in text  # a points-only turn never names the paperwork agent


def test_footer_absent_on_fallback(monkeypatch):
    monkeypatch.setenv("USE_REMOTE_AGENTS", "true")
    monkeypatch.setenv("POINTS_AGENT_ADDRESS", "agent1qpoints")
    monkeypatch.setenv("PAPERWORK_AGENT_ADDRESS", "agent1qpaperwork")

    class _DownCtx(_FakeAgentCtx):
        async def send_and_receive(self, *a, **k):
            return None, object()  # specialist unreachable → fallback

    ctx = _DownCtx()
    asyncio.run(on_chat_message(ctx, "agent1qsender", _msg(GOLDEN0)))
    before = len(ctx.sent)
    asyncio.run(on_chat_message(ctx, "agent1qsender", _msg(ODDS)))
    text = "\n".join(
        c.text for m in ctx.sent[before:] if isinstance(m, ChatMessage)
        for c in m.content if isinstance(c, TextContent)
    )
    assert "puntos" in text          # correct local answer
    assert "Consulté" not in text    # never a footer on fallback
