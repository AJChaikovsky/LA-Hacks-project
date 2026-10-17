"""ASI:One client (https://api.asi1.ai/v1, model asi1): timeout, one retry, never raises.

Owner: Dev C, ticket C0 (#18).

    complete_text(prompt, system=None, model=None) -> str | None
    complete_json(prompt, schema, fallback, system=None, model=None) -> schema instance

Both return a safe value instead of raising, so a slow or broken LLM can never take the
agent down. Every fallback logs a line starting with [LLM_FALLBACK].

JSON mode: the first complete_json call tries response_format={"type": "json_object"}.
If ASI:One rejects it with a 400, we switch to prompt-only JSON for the rest of the
process. Set ASI1_JSON_MODE=on/off in .env to skip the probe (scripts/llm_smoke.py
tells you which one works).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import TypeVar

import openai
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

log = logging.getLogger("llm")

BASE_URL = "https://api.asi1.ai/v1"
T = TypeVar("T", bound=BaseModel)

_client: openai.OpenAI | None = None
_json_mode: bool | None = {"on": True, "off": False}.get(os.getenv("ASI1_JSON_MODE", "").lower())

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def default_model() -> str:
    return os.getenv("ASI1_MODEL", "asi1")


def get_client() -> openai.OpenAI | None:
    """Shared client, or None when ASI1_API_KEY is missing."""
    global _client
    if _client is None:
        key = os.getenv("ASI1_API_KEY")
        if not key:
            return None
        _client = openai.OpenAI(
            api_key=key,
            base_url=BASE_URL,
            timeout=float(os.getenv("LLM_TIMEOUT_S", "12")),
            max_retries=1,
        )
    return _client


def _messages(prompt: str, system: str | None) -> list[dict]:
    msgs = [{"role": "system", "content": system}] if system else []
    return msgs + [{"role": "user", "content": prompt}]


def complete_text(prompt: str, system: str | None = None, model: str | None = None) -> str | None:
    """Plain completion. Returns None (and logs) on any failure."""
    client = get_client()
    if client is None:
        log.warning("[LLM_FALLBACK] no ASI1_API_KEY")
        return None
    start = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model or default_model(), messages=_messages(prompt, system), temperature=0,
        )
        text = resp.choices[0].message.content
    except Exception as exc:  # noqa: BLE001 - never raise into the agent
        log.warning("[LLM_FALLBACK] text call failed: %s", exc)
        return None
    log.info("[LLM] text ok in %.1fs", time.monotonic() - start)
    return text


def extract_json_block(text: str | None) -> dict | None:
    """Best-effort: whole text, then a ```json fence, then the outermost {...} span."""
    if not text:
        return None
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first:last + 1])
    for c in candidates:
        try:
            data = json.loads(c)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _json_instructions(schema: type[BaseModel]) -> str:
    return (
        "Respond with ONLY one JSON object, no prose and no code fences. "
        "It must match this JSON Schema; omit fields you don't know:\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )


def complete_json(
    prompt: str,
    schema: type[T],
    fallback: T,
    system: str | None = None,
    model: str | None = None,
) -> T:
    """Ask for a JSON object matching `schema`. Returns `fallback` on any failure."""
    global _json_mode
    client = get_client()
    if client is None:
        log.warning("[LLM_FALLBACK] no ASI1_API_KEY")
        return fallback

    system_full = "\n\n".join(s for s in (system, _json_instructions(schema)) if s)
    kwargs = dict(model=model or default_model(), messages=_messages(prompt, system_full), temperature=0)
    start = time.monotonic()
    try:
        if _json_mode is not False:
            try:
                resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
                _json_mode = True
            except openai.BadRequestError as exc:
                log.info("[LLM] JSON mode rejected (%s); using prompt-only JSON from now on", exc)
                _json_mode = False
                resp = client.chat.completions.create(**kwargs)
        else:
            resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content
    except Exception as exc:  # noqa: BLE001 - never raise into the agent
        log.warning("[LLM_FALLBACK] json call failed: %s", exc)
        return fallback

    data = extract_json_block(text)
    if data is None:
        log.warning("[LLM_FALLBACK] no JSON object in reply: %r", (text or "")[:200])
        return fallback
    try:
        result = schema.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("[LLM_FALLBACK] reply failed %s validation: %s", schema.__name__, exc)
        return fallback
    log.info("[LLM] json ok in %.1fs (json_mode=%s)", time.monotonic() - start, _json_mode)
    return result
