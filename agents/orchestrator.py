"""choices-counselor: Chat Protocol agent that owns the conversation and calls the specialists.

Owner: Dev A, tickets A1-A3 (#2, #3, #4). Must match the signature in core/stubs.py where one is listed.

A1: mailbox agent that acks every ChatMessage first, then replies with the stub profile
summary (core/stubs.py fixtures). Reply language comes from core.extract.detect_language;
an undetectable language gets English then Spanish. Session state and routing are A2.

Run: .venv/bin/python agents/orchestrator.py   (needs COUNSELOR_SEED in .env)
"""
from __future__ import annotations

import os
import sys
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
from core.extract import detect_language
from core.types import FamilyProfile

load_dotenv()

SEED = os.getenv("COUNSELOR_SEED", "")
if not SEED:
    raise SystemExit("COUNSELOR_SEED is empty: set it in .env before running the counselor agent.")

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
_SORRY = {
    "en": "Sorry, something went wrong on my side. Please send your message again.",
    "es": "Perdón, algo salió mal de mi lado. Por favor envíe su mensaje otra vez.",
}


def _pick(table: dict[str, str], lang: str | None) -> str:
    """One language when detected, otherwise English then Spanish."""
    if lang in table:
        return table[lang]
    return table["en"] + "\n\n" + table["es"]


def _summary_block(profile: FamilyProfile, schools: str, lang: str) -> str:
    interests = ", ".join(profile.interests)
    if lang == "es":
        lines = [
            f"Esto es lo que entendí sobre {profile.child_name or 'su hijo/a'}:",
            f"- Grado actual: {profile.current_grade} → aplica al grado {profile.applying_grade}",
            f"- Intereses: {interests or 'aún sin datos'}",
            f"- Vecindario: {profile.home_neighborhood or 'aún sin datos'}",
            "Programas que podrían encajar:",
            schools,
        ]
    else:
        lines = [
            f"Here's what I understood about {profile.child_name or 'your child'}:",
            f"- Current grade: {profile.current_grade} → applying for grade {profile.applying_grade}",
            f"- Interests: {interests or 'none yet'}",
            f"- Neighborhood: {profile.home_neighborhood or 'none yet'}",
            "Programs that could fit:",
            schools,
        ]
    return "\n".join(lines)


def _stub_summary(text: str, lang: str | None) -> str:
    profile = stubs.extract(text, None)
    schools = stubs.render_matches(stubs.match(profile), profile)
    langs = [lang] if lang in ("en", "es") else ["en", "es"]
    return "\n\n".join(_summary_block(profile, schools, lang) for lang in langs)


@chat_proto.on_message(ChatMessage)
async def on_chat_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
    try:
        text = msg.text().strip()
        lang = detect_language(text) if text else None
        ctx.logger.info("ChatMessage %s from %s: text=%r lang=%s", msg.msg_id, sender, text[:80], lang or "both")
        parts: list[str] = []
        for item in msg.content:
            if isinstance(item, StartSessionContent):
                parts.append(_pick(_WELCOME, lang))
            elif isinstance(item, EndSessionContent):
                ctx.logger.info("session ended by %s", sender)
        if text:
            parts.append(_stub_summary(text, lang))
        if parts:
            reply = "\n\n".join(parts)
            await ctx.send(sender, ChatMessage(content=[TextContent(text=reply)]))
            ctx.logger.info("replied to %s (%d chars, lang=%s)", sender, len(reply), lang or "both")
    except Exception:
        ctx.logger.exception("handler failed for %s from %s", msg.msg_id, sender)
        try:
            await ctx.send(sender, ChatMessage(content=[TextContent(text=_pick(_SORRY, None))]))
        except Exception:
            ctx.logger.exception("could not send apology to %s", sender)
            return


@chat_proto.on_message(ChatAcknowledgement)
async def on_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.debug("ack from %s for %s", sender, msg.acknowledged_msg_id)


agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
