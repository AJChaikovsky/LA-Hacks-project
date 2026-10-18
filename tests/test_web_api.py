"""POST /chat (replay_golden.py --live) runs the same handle_turn as ASI:One. Offline, no network."""
from __future__ import annotations

import asyncio
import json
import logging
import os

os.environ.setdefault("COUNSELOR_SEED", "web-test-seed-not-production")

import pytest

from agents import web_api
from agents.web_api import WebChatRequest, web_turn
from core import geo

SESSION = "3f2b9c1e-0000-4000-8000-000000000001"


class FakeStorage(dict):
    def set(self, key, value):
        self[key] = value

    def has(self, key):
        return key in self


class FakeCtx:
    def __init__(self):
        self.storage = FakeStorage()
        self.logger = logging.getLogger("web-test")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for flag in ("INTENT_LLM", "EXTRACT_LLM", "EMAIL_LLM"):
        monkeypatch.setenv(flag, "off")
    monkeypatch.setenv("USE_STUBS", "false")


def turn(ctx, text, session=SESSION):
    return asyncio.run(web_turn(ctx, WebChatRequest(session_id=session, text=text)))


def test_golden_conversation_over_the_web_endpoint():
    ctx = FakeCtx()
    r1 = turn(ctx, "Hi, I'm Maria. We live in Boyle Heights. My son Diego is in 5th grade and has an IEP. "
                   "He loves art and drawing. Which schools do you recommend?")
    assert r1.error is None and r1.stage == "matched" and r1.language == "en"
    carousel = next(r for r in r1.replies if r["kind"] == "carousel")
    assert len(carousel["payload"]["items"]) == 3
    assert f"web:{SESSION}" in ctx.storage

    click = json.dumps(carousel["payload"]["items"][0]["primary_cta"]["selection"])
    assert any(r["kind"] == "detail" for r in turn(ctx, click).replies)
    assert "points" in turn(ctx, "What are his chances of getting in?").replies[0]["text"].lower()
    plan = turn(ctx, "Ok, only the three. What do I need to finish?")
    assert plan.stage == "plan" and any(r["kind"] == "review" for r in plan.replies)


def test_sessions_are_separate_and_namespaced():
    ctx = FakeCtx()
    turn(ctx, "My daughter is in 5th grade, we live in Sylmar, she loves science", session="aaaaaaaa-1")
    turn(ctx, "Hola", session="bbbbbbbb-2")
    assert set(ctx.storage) == {"web:aaaaaaaa-1", "web:bbbbbbbb-2"}
    assert "Sylmar" not in ctx.storage["web:bbbbbbbb-2"]


@pytest.mark.parametrize("session,text", [("x", "hi"), ("../../etc", "hi"), (SESSION, "   "), (SESSION, "a" * 5000)])
def test_bad_requests_are_refused_without_touching_state(session, text):
    ctx = FakeCtx()
    r = turn(ctx, text, session=session)
    assert r.error == "bad request" and r.replies == [] and not ctx.storage


def test_failed_turn_apologizes_and_keeps_state(monkeypatch):
    async def boom(*a, **k):
        raise TimeoutError
    monkeypatch.setattr(web_api, "bounded_turn", boom)
    ctx = FakeCtx()
    r = turn(ctx, "hello")
    assert "(213) 241-4177" in r.replies[0]["text"] and not ctx.storage


def test_form_address_is_used_but_never_logged_or_stored(monkeypatch, caplog):
    geo._cache.clear()
    monkeypatch.setattr(geo, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(geo, "_fetch", lambda address: (34.042, -118.218))
    ctx = FakeCtx()
    form = json.dumps({"action": "intake_form", "values": {
        "grade": "5", "neighborhood": "Boyle Heights", "interest": "arts", "address": "1831 E 1st St"}})
    with caplog.at_level(logging.DEBUG):
        r = turn(ctx, form)
    assert r.stage == "matched"
    stored = ctx.storage[f"web:{SESSION}"]
    assert "1831" not in stored and "1831" not in caplog.text
    assert json.loads(stored)["profile"]["home_lat"] == 34.042
    geo._cache.clear()


def test_chat_endpoint_registers_on_the_counselor():
    from uagents import Agent
    loop = asyncio.new_event_loop()
    agent = Agent(name="web-test", seed="web-test-seed-not-production-2", loop=loop)
    web_api.register_web(agent)
    assert agent._server.has_rest_endpoint("POST", "/chat")
    assert not agent._server.has_rest_endpoint("GET", "/info")
    loop.close()
