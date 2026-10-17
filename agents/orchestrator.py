"""choices-counselor: Chat Protocol agent that owns the conversation and calls the specialists.

Owner: Dev A, tickets A1-A3 (#2, #3, #4). Must match the signature in core/stubs.py where one is listed.

A2: per-conversation sessions in ctx.storage, intent routing through pure handlers
(mirrors Dev C's reference scripts/chat_local.py handle()), bounded turn execution,
hotline apology on any failure. Core calls stay on core/stubs.py until A3 wires the
real modules.

Pre-demo reset: stop the agent, delete agent1q*_data.json, restart.

Run: .venv/bin/python agents/orchestrator.py   (needs COUNSELOR_SEED in .env)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

from core import stubs
from core.extract import detect_language, parse_school_list
from core.intent import classify, parse_card_selection, scope_reply
from core.types import FamilyProfile, SessionState

load_dotenv()

SEED = os.getenv("COUNSELOR_SEED", "")
if not SEED:
    raise SystemExit("COUNSELOR_SEED is empty: set it in .env before running the counselor agent.")

# Outer bound on a whole turn; each LLM call inside is already bounded by core/llm.py.
TURN_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "12")) + 3

agent = Agent(
    name="choices-counselor",
    seed=SEED,
    mailbox=True,
    publish_agent_details=True,
)

chat_proto = Protocol(spec=chat_protocol_spec)

_WELCOME = {
    "en": (
        "Hello! I'm the LAUSD Choices counselor. Tell me about your child — grade, "
        "interests, neighborhood — and I'll suggest Choices programs before the Nov 6 deadline."
    ),
    "es": (
        "¡Hola! Soy el consejero de Choices de LAUSD. Cuénteme de su hijo o hija — grado, "
        "intereses, vecindario — y le sugiero programas de Choices antes del 6 de noviembre."
    ),
}
# Hotline per BUILD_PLAN.md:392; number verified against i18n/es.json:63 and i18n/en.json:63.
_SORRY = {
    "en": (
        "Sorry — something went wrong on my side. Please try again, or call the "
        "LAUSD Choices Support Line at (213) 241-4177."
    ),
    "es": (
        "Perdón — algo salió mal de mi lado. Inténtelo otra vez o llame a la línea "
        "de ayuda de Choices de LAUSD al (213) 241-4177."
    ),
}
_HELP = {
    "en": (
        "I help with the LAUSD Choices application: I find 3 schools for your child, estimate "
        "points, check your list, and prepare documents, dates, and an email. Tell me about "
        "your child to start."
    ),
    "es": (
        "Le ayudo con la solicitud Choices de LAUSD: busco 3 escuelas para su hijo o hija, "
        "calculo sus puntos, reviso su lista y le preparo documentos, fechas y un correo. "
        "Cuénteme de su hijo o hija para empezar."
    ),
}


def _pick(table: dict[str, str], lang: str | None) -> str:
    """One language when detected, otherwise English then Spanish."""
    if lang in table:
        return table[lang]
    return table["en"] + "\n\n" + table["es"]


def _lang(profile: FamilyProfile) -> str:
    return profile.language if profile.language in ("es", "en") else "en"


def session_key(sender: str, ctx_session) -> str:
    """The one place that decides the session key format; nothing else depends on it."""
    if ctx_session is None:
        return f"session:{sender}"
    return f"session:{sender}:{ctx_session}"


def split_reply(text: str, limit: int = 3500) -> list[str]:
    """A reply over ~limit chars goes out as two ChatMessages, split at a break."""
    if len(text) <= limit:
        return [text]
    cut = text.rfind("\n\n", 0, limit)
    if cut <= 0:
        cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit
    return [text[:cut].rstrip(), text[cut:].lstrip()]


def handle_turn(state: SessionState, text: str, today: date | None = None) -> tuple[SessionState, list[str], str]:
    """One conversation turn: (new state, reply parts, intent). Pure, no uAgents, no storage."""
    today = today or date.today()
    intent = classify(text, state.stage)
    p = state.profile.model_copy(update={"language": detect_language(text) or state.profile.language})
    state = state.model_copy(update={"profile": p})
    lang = _lang(p)

    if intent == "reset":
        return SessionState(), [_HELP[lang]], intent

    if intent == "profile_info":
        p = stubs.extract(text, p)  # A3 swaps in the real modules when USE_STUBS=false
        matches = stubs.match(p)
        state = state.model_copy(update={"profile": p, "matches": matches, "stage": "matched"})
        return state, [stubs.render_matches(matches, p)], intent

    if intent in ("ask_odds", "set_list", "ask_plan") and not state.matches:
        return state, [scope_reply("intake", lang)], intent

    codes = p.intended_choices or [m.code for m in state.matches]

    if intent == "ask_odds":
        estimates = stubs.estimate(p, codes)
        state = state.model_copy(update={"estimates": estimates, "stage": "odds"})
        return state, [stubs.render_points(estimates, p)], intent

    if intent == "set_list":
        choices, doubtful = parse_school_list(text, [m.code for m in state.matches])
        choices = choices or codes
        p = p.model_copy(update={
            "intended_choices": choices,
            "would_not_accept": list(dict.fromkeys(p.would_not_accept + doubtful)),
        })
        warnings = stubs.check_list(p, choices)
        state = state.model_copy(update={"profile": p, "warnings": warnings, "stage": "listing"})
        return state, [stubs.render_warnings(warnings, p)], intent

    if intent == "ask_plan":
        choices, _ = parse_school_list(text, [m.code for m in state.matches])
        if choices:
            p = p.model_copy(update={"intended_choices": choices})
        plan = stubs.build_plan(p, p.intended_choices or codes, lang, today)
        state = state.model_copy(update={"profile": p, "plan": plan, "stage": "plan"})
        return state, [stubs.render_plan(plan, p)], intent

    if intent == "card_selection":
        sel = parse_card_selection(text) or {}
        m = next((m for m in state.matches if m.code == sel.get("code")), None)
        if m:
            contact = " · ".join(s for s in (m.address, m.phone) if s)
            return state, [f"**{m.name} · {m.theme}**" + (f"\n{contact}" if contact else "")], intent
        return state, [scope_reply(state.stage, lang)], intent

    if intent == "help":
        return state, [_HELP[lang]], intent

    return state, [scope_reply(state.stage, lang)], intent


async def bounded_turn(
    state: SessionState, text: str, today: date | None = None, timeout: float | None = None
) -> tuple[SessionState, list[str], str]:
    """handle_turn in a worker thread with a deadline. Raises TimeoutError or the handler's error;
    on either, the caller must not write state (a late thread result is discarded by wait_for)."""
    return await asyncio.wait_for(asyncio.to_thread(handle_turn, state, text, today), timeout or TURN_TIMEOUT_S)


def _load_state(ctx: Context, key: str) -> SessionState:
    raw = ctx.storage.get(key)
    if raw:
        try:
            return SessionState.model_validate_json(raw)
        except Exception:
            ctx.logger.warning("corrupt session state at %s; starting fresh", key)
    return SessionState()


async def _apologize(ctx: Context, sender: str, lang: str | None) -> None:
    try:
        await ctx.send(sender, ChatMessage(content=[TextContent(text=_pick(_SORRY, lang))]))
    except Exception:
        ctx.logger.exception("could not send apology to %s", sender)


@chat_proto.on_message(ChatMessage)
async def on_chat_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
    key = session_key(sender, ctx.session)
    lang: str | None = None
    try:
        text = msg.text().strip()
        parts: list[str] = []
        for item in msg.content:
            if isinstance(item, StartSessionContent):
                if ctx.storage.has(key):
                    ctx.storage.remove(key)  # fresh chat: reset without writing state
                parts.append(_pick(_WELCOME, detect_language(text) if text else None))
            elif isinstance(item, EndSessionContent):
                ctx.logger.info("session ended by %s", sender)
        state = _load_state(ctx, key)
        lang = _lang(state.profile)
        intent = "none"
        if text:
            state, reply_parts, intent = await bounded_turn(state, text)
            lang = _lang(state.profile)
            parts.extend(reply_parts)
            ctx.storage.set(key, state.model_dump_json())  # written ONLY after an in-time success
        ctx.logger.info(
            "turn sender=%s session=%s key=%s intent=%s stage=%s",
            sender, ctx.session, key, intent, state.stage,
        )
        if parts:
            for chunk in split_reply("\n\n".join(parts)):
                await ctx.send(sender, ChatMessage(content=[TextContent(text=chunk)]))
    except TimeoutError:
        ctx.logger.warning("turn timed out (>%.0fs) for %s; state untouched", TURN_TIMEOUT_S, sender)
        await _apologize(ctx, sender, lang)
    except Exception:
        ctx.logger.exception("handler failed for %s from %s", msg.msg_id, sender)
        await _apologize(ctx, sender, lang)


@chat_proto.on_message(ChatAcknowledgement)
async def on_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.debug("ack from %s for %s", sender, msg.acknowledged_msg_id)


agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
