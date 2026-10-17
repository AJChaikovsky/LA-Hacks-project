"""core/llm.py never raises and always returns a usable value. No network: a fake client stands in."""
import logging
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from core import llm


class Small(BaseModel):
    grade: int | None = None
    language: str = "en"


FALLBACK = Small(grade=None, language="en")


def reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class FakeClient:
    """Mimics client.chat.completions.create; `script` items are reply text or an exception."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return reply(item)


def bad_request():
    req = httpx.Request("POST", "https://api.asi1.ai/v1/chat/completions")
    return openai.BadRequestError("response_format not supported", response=httpx.Response(400, request=req), body=None)


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(llm, "_json_mode", None)
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setenv("ASI1_API_KEY", "test-key")


def use(monkeypatch, client):
    monkeypatch.setattr(llm, "get_client", lambda: client)
    return client


def test_json_mode_success(monkeypatch):
    client = use(monkeypatch, FakeClient('{"grade": 5, "language": "es"}'))
    assert llm.complete_json("Diego está en quinto", Small, FALLBACK) == Small(grade=5, language="es")
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert client.calls[0]["temperature"] == 0
    assert llm._json_mode is True


def test_json_mode_rejected_switches_to_prompt_only(monkeypatch):
    client = use(monkeypatch, FakeClient(bad_request(), 'Claro:\n```json\n{"grade": 5}\n```', '{"grade": 6}'))
    assert llm.complete_json("x", Small, FALLBACK).grade == 5
    assert "response_format" not in client.calls[1]
    assert llm._json_mode is False
    llm.complete_json("y", Small, FALLBACK)
    assert "response_format" not in client.calls[2]  # remembered for the rest of the process


def test_schema_is_in_the_system_prompt(monkeypatch):
    client = use(monkeypatch, FakeClient('{"grade": 5}'))
    llm.complete_json("x", Small, FALLBACK, system="You extract school facts.")
    system = client.calls[0]["messages"][0]["content"]
    assert system.startswith("You extract school facts.") and '"grade"' in system


@pytest.mark.parametrize("script", [
    ["not json at all"],
    ['{"grade": "fifth"}'],          # fails validation
    [RuntimeError("connection reset")],
    [openai.APITimeoutError(request=httpx.Request("POST", "https://api.asi1.ai"))],
])
def test_failures_return_fallback_and_log(monkeypatch, caplog, script):
    use(monkeypatch, FakeClient(*script))
    with caplog.at_level(logging.WARNING, logger="llm"):
        assert llm.complete_json("x", Small, FALLBACK) is FALLBACK
    assert "[LLM_FALLBACK]" in caplog.text


def test_missing_key_returns_fallback(monkeypatch, caplog):
    monkeypatch.delenv("ASI1_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger="llm"):
        assert llm.complete_json("x", Small, FALLBACK) is FALLBACK
        assert llm.complete_text("x") is None
    assert "no ASI1_API_KEY" in caplog.text


def test_complete_text(monkeypatch):
    use(monkeypatch, FakeClient("Hola Maria"))
    assert llm.complete_text("hola") == "Hola Maria"
    use(monkeypatch, FakeClient(RuntimeError("boom")))
    assert llm.complete_text("hola") is None


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go: {"a": {"b": 2}} thanks', {"a": {"b": 2}}),
    ("[1, 2]", None),
    ("", None),
    (None, None),
])
def test_extract_json_block(text, expected):
    assert llm.extract_json_block(text) == expected
