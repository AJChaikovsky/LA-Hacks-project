"""Website front end for choices-counselor: a REST endpoint on the same agent ASI:One talks to.

Owner: Dev B (website option 1). The page in web/index.html POSTs each message to /chat; the turn runs
through the same bounded_turn/handle_turn as ASI:One, so there is one agent and one code path. Card
clicks come back as the card's selection JSON, exactly like an ASI:One click.

    POST /chat  {"session_id": "<uuid>", "text": "..."}  ->  {"session_id", "replies": [...], "stage", "language"}
    GET  /info  ->  agent address and the welcome text

Web sessions are stored under "web:<session_id>" so they never collide with ASI:One senders.
Never logs the message text (a form submission can carry a street address).
"""
from __future__ import annotations

import re

from uagents import Agent, Context, Model

from agents.cards import validate
from agents.orchestrator import _SORRY, _WELCOME, _lang, _load_state, _pick, bounded_turn

MAX_TEXT = 4000
_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class WebChatRequest(Model):
    session_id: str
    text: str


class WebChatResponse(Model):
    session_id: str
    replies: list[dict]     # {"text": str, "kind": str | None, "payload": dict | None}
    stage: str
    language: str | None = None
    error: str | None = None


class WebInfo(Model):
    agent: str
    welcome: dict


async def web_turn(ctx: Context, req: WebChatRequest) -> WebChatResponse:
    text = (req.text or "").strip()
    if not _SESSION_ID.match(req.session_id or "") or not text or len(text) > MAX_TEXT:
        return WebChatResponse(session_id=req.session_id or "", replies=[], stage="intake", error="bad request")
    key = f"web:{req.session_id}"
    state = _load_state(ctx, key)
    try:
        state, parts, intent = await bounded_turn(state, text)
    except Exception as exc:  # timeout or handler error: apologize, keep the old state
        ctx.logger.warning("web turn failed (%s) session=%s", type(exc).__name__, req.session_id[:8])
        lang = _lang(state.profile)
        return WebChatResponse(session_id=req.session_id, stage=state.stage, language=lang,
                               replies=[{"text": _pick(_SORRY, lang), "kind": None, "payload": None}])
    ctx.storage.set(key, state.model_dump_json())  # written only after an in-time success
    ctx.logger.info("web turn session=%s intent=%s stage=%s", req.session_id[:8], intent, state.stage)
    replies = []
    for part in parts:
        card = part.kind and part.payload is not None and not validate(part.kind, part.payload)
        replies.append({"text": part.narration, "kind": part.kind if card else None,
                        "payload": part.payload if card else None})
    return WebChatResponse(session_id=req.session_id, replies=replies, stage=state.stage,
                           language=state.profile.language)


def register_web(agent: Agent) -> None:
    """Add POST /chat and GET /info to the counselor; served on the agent's own HTTP port (CORS open)."""

    @agent.on_rest_post("/chat", WebChatRequest, WebChatResponse)
    async def chat(ctx: Context, req: WebChatRequest) -> WebChatResponse:
        return await web_turn(ctx, req)

    @agent.on_rest_get("/info", WebInfo)
    async def info(ctx: Context) -> WebInfo:
        return WebInfo(agent=agent.address, welcome=_WELCOME)
