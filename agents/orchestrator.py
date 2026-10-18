"""choices-counselor: Chat Protocol agent that owns the conversation and calls the specialists.

Owner: Dev A, tickets A1-A3 (#2, #3, #4). Must match the signature in core/stubs.py where one is listed.

A3: the golden path wired in-process. handle_turn mirrors the reference scripts/chat_local.handle()
but calls the real core/ modules and emits real Interactive Cards (agents/cards.py) instead of text
previews. A2's machinery (ack-first, per-conversation sessions, bounded turns, loop guard, hotline
apology) is unchanged. Core logic is real unless USE_STUBS=true (a dev-only escape hatch).

Pre-demo reset: stop the agent, delete agent1q*_data.json, restart.

Run: .venv/bin/python agents/orchestrator.py   (needs COUNSELOR_SEED in .env)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

from agents.cards import (
    card_message,
    intake_form,
    matches_carousel,
    packet_review,
    respond_to_selection,
)
from agents.local_resolver import LocalFirst, local_url, register_health, register_self_check
from agents.messages import PaperworkRequest, PaperworkResponse, PointsRequest, PointsResponse
from core import guardrails, matcher, paperwork, points, render, stubs
from core.brochure_qa import answer as brochure_answer, render_answer as render_brochure_answer
from core.geo import apply_address
from core.extract import detect_language, extract as extract_profile, merge, parse_school_list
from core.intent import classify as classify_intent, parse_card_selection, scope_reply
from core.types import ActionPlan, FamilyProfile, GuardrailWarning, PointsEstimate, SessionState

load_dotenv()

# Outer bound on a whole turn; each LLM call inside is already bounded by core/llm.py.
TURN_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "12")) + 3

# Specialist agents (A4). Each remote call falls back to the in-process core/ function.
REMOTE_TIMEOUT_S = float(os.getenv("REMOTE_TIMEOUT_S", "4"))
_FOOTER_LABEL = {
    "points": {"es": "Agente de Puntos", "en": "Points Agent"},
    "paperwork": {"es": "Agente de Trámites", "en": "Paperwork Agent"},
}
_FOOTER_PREFIX = {"es": "Consulté:", "en": "Consulted:"}

# Loop guard: stop replying once a session hits this many consecutive no-progress
# greeting/off-topic turns (we still ack). Default 4 leaves a real person room to ask 3
# "what can you do?" questions and still get 3 answers; the bot ping-pong dies on the 4th.
GREETING_INTENTS = frozenset({"help", "off_topic"})
SUPPRESS_AFTER_IDLE = int(os.getenv("LOOP_SUPPRESS_IDLE", "4"))

_MENTION = re.compile(r"@agent1[0-9a-z]+")

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
_ASK_MORE = {
    "en": "To find schools I need your neighborhood or what your child enjoys. Can you tell me?",
    "es": "Para buscar escuelas necesito su barrio o qué le gusta a su hijo o hija. ¿Me cuenta?",
}


# ---------------------------------------------------------------------------
# Real vs stub dispatch. Demo path is real; USE_STUBS=true is a dev escape hatch.
# ---------------------------------------------------------------------------


def use_stubs() -> bool:
    return os.getenv("USE_STUBS", "false").strip().lower() in ("1", "true", "yes", "on")


def _classify(text: str, stage: str) -> str:
    return stubs.classify(text, stage) if use_stubs() else classify_intent(text, stage)


def _extract(text: str, prior: FamilyProfile) -> FamilyProfile:
    return stubs.extract(text, prior) if use_stubs() else extract_profile(text, prior)


def _match(p: FamilyProfile):
    return stubs.match(p) if use_stubs() else matcher.match(p)


def _estimate(p: FamilyProfile, codes: list[str]):
    return stubs.estimate(p, codes) if use_stubs() else points.estimate(p, codes)


def _check_list(p: FamilyProfile, codes: list[str], today: date):
    return stubs.check_list(p, codes) if use_stubs() else guardrails.check_list(p, codes, today)


def _build_plan(p: FamilyProfile, codes: list[str], lang: str, today: date):
    return stubs.build_plan(p, codes, lang, today) if use_stubs() else paperwork.build_plan(p, codes, lang, today)


# ---------------------------------------------------------------------------
# Remote specialists (A4). handle_turn runs in a worker thread (bounded_turn);
# send_and_receive is async on the agent loop, so each call bridges thread→loop with
# run_coroutine_threadsafe. Any failure (down agent, timeout, bad reply) → core/ fallback.
# ---------------------------------------------------------------------------


def use_remote_agents() -> bool:
    return os.getenv("USE_REMOTE_AGENTS", "true").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class RemoteTools:
    ctx: Context
    loop: asyncio.AbstractEventLoop
    session_id: str
    points_addr: str
    paperwork_addr: str
    timeout: float = REMOTE_TIMEOUT_S
    consulted: set = field(default_factory=set)

    def _roundtrip(self, addr: str, req, response_type, label: str):
        """One specialist round-trip from the worker thread. Returns the response or None."""
        start = time.monotonic()
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self.ctx.send_and_receive(addr, req, response_type=response_type, timeout=int(self.timeout)),
                self.loop,
            )
            resp, _status = fut.result(timeout=self.timeout + 2)
        except Exception as exc:  # noqa: BLE001 - any failure is just a fallback
            self.ctx.logger.info("[FALLBACK] choices-%s errored (%s) -> local core/", label, type(exc).__name__)
            return None
        dt = (time.monotonic() - start) * 1000
        if resp is None:
            self.ctx.logger.info("[FALLBACK] choices-%s no reply in %.0fms -> local core/", label, dt)
            return None
        self.consulted.add(label)
        self.ctx.logger.info("[HANDOFF] counselor -> choices-%s %s (%.0fms) remote", label, addr[:20], dt)
        return resp

    def estimate(self, p: FamilyProfile, codes: list[str]):
        if not self.points_addr:
            return _estimate(p, codes)
        req = PointsRequest(session_id=self.session_id, profile_json=p.model_dump_json(), codes=list(codes))
        resp = self._roundtrip(self.points_addr, req, PointsResponse, "points")
        if resp is None:
            return _estimate(p, codes)
        return [PointsEstimate.model_validate(e) for e in json.loads(resp.estimates_json)]

    def check_list(self, p: FamilyProfile, codes: list[str], today: date):
        if not self.points_addr:
            return _check_list(p, codes, today)
        req = PointsRequest(session_id=self.session_id, profile_json=p.model_dump_json(), codes=list(codes))
        resp = self._roundtrip(self.points_addr, req, PointsResponse, "points")
        if resp is None:
            return _check_list(p, codes, today)
        return [GuardrailWarning.model_validate(w) for w in json.loads(resp.warnings_json)]

    def build_plan(self, p: FamilyProfile, codes: list[str], lang: str, today: date):
        if not self.paperwork_addr:
            return _build_plan(p, codes, lang, today)
        req = PaperworkRequest(
            session_id=self.session_id, profile_json=p.model_dump_json(), codes=list(codes), language=lang
        )
        resp = self._roundtrip(self.paperwork_addr, req, PaperworkResponse, "paperwork")
        if resp is None:
            return _build_plan(p, codes, lang, today)
        return ActionPlan.model_validate_json(resp.plan_json)


def _footer(consulted: set, lang: str) -> str:
    """"Consulté: …" listing ONLY the specialists that genuinely replied this turn."""
    lang = lang if lang in _FOOTER_PREFIX else "en"
    labels = [_FOOTER_LABEL[k][lang] for k in ("points", "paperwork") if k in consulted]
    return f"{_FOOTER_PREFIX[lang]} {' · '.join(labels)}"


def _build_remote(ctx: Context) -> "RemoteTools | None":
    if not use_remote_agents():
        return None
    pa = os.getenv("POINTS_AGENT_ADDRESS", "").strip()
    pw = os.getenv("PAPERWORK_AGENT_ADDRESS", "").strip()
    if not pa and not pw:
        return None
    return RemoteTools(
        ctx=ctx, loop=asyncio.get_running_loop(), session_id=str(ctx.session),
        points_addr=pa, paperwork_addr=pw, timeout=REMOTE_TIMEOUT_S,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick(table: dict[str, str], lang: str | None) -> str:
    """One language when detected, otherwise English then Spanish."""
    if lang in table:
        return table[lang]
    return table["en"] + "\n\n" + table["es"]


def _lang(profile: FamilyProfile) -> str:
    return profile.language if profile.language in ("es", "en") else "en"


def strip_mention(text: str) -> str:
    """Drop @agent1… mentions so the logged text shows the real message, not the address."""
    return _MENTION.sub("", text or "").strip()


def guard_decision(
    prev_stage: str, new_stage: str, intent: str, prev_idle: int, threshold: int | None = None
) -> tuple[bool, int]:
    """Count-based loop guard: (send_reply, new_idle_count).

    No timing data was recoverable from the log (uAgents logs carry no per-turn timestamp),
    so we can't tell a fast bot loop from a slow human by gap; we cap consecutive no-progress
    greeting/off-topic turns instead. Any stage change (real progress) resets the counter.
    """
    threshold = SUPPRESS_AFTER_IDLE if threshold is None else threshold
    if new_stage != prev_stage:
        return True, 0
    if intent in GREETING_INTENTS:
        new_idle = prev_idle + 1
        return new_idle < threshold, new_idle
    return True, 0


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


# ---------------------------------------------------------------------------
# Turn routing (mirrors scripts/chat_local.handle; real modules + real cards)
# ---------------------------------------------------------------------------


@dataclass
class ReplyPart:
    """One reply unit: narration text plus an optional Interactive Card (kind + payload)."""

    narration: str
    kind: str | None = None
    payload: dict | None = None


def _match_step(state: SessionState, p: FamilyProfile, intent: str) -> tuple[SessionState, list[ReplyPart], str]:
    """After profile facts arrive (text or intake form): ask for what's missing, else match."""
    lang = _lang(p)
    state = state.model_copy(update={"profile": p})
    if not p.applying_grade:  # never assume middle school: ask, and send the intake form card
        return state, [ReplyPart(render.render_ask_grade(p), "form", intake_form(p))], intent
    if not (p.interests or p.home_neighborhood):
        return state, [ReplyPart(_ASK_MORE[lang], "form", intake_form(p))], intent
    matches = _match(p)
    # Zero matches: stay at intake so the parent can fix the grade; no odds offer.
    state = state.model_copy(update={"matches": matches, "stage": "matched" if matches else "intake"})
    if not matches:
        return state, [ReplyPart(render.render_no_matches(p))], intent
    return state, [ReplyPart(render.render_matches(matches, p), "carousel", matches_carousel(matches, p))], intent


def handle_turn(
    state: SessionState, text: str, today: date | None = None, remote: "RemoteTools | None" = None
) -> tuple[SessionState, list[ReplyPart], str]:
    """One conversation turn: (new state, reply parts, intent). Pure: no uAgents, no storage, no I/O.

    `remote`, when given, routes estimate/check_list/build_plan to the specialist agents with an
    in-process fallback; `None` keeps everything local (tests, offline golden)."""
    today = today or date.today()
    intent = _classify(text, state.stage)
    # Reply in the language of the latest message; a card click (JSON / button label) carries no
    # language signal, so it keeps the current one.
    spoken = None if parse_card_selection(text) is not None else detect_language(text)
    p = state.profile.model_copy(update={"language": spoken or state.profile.language})
    state = state.model_copy(update={"profile": p})
    lang = _lang(p)

    if intent == "reset":
        return SessionState(), [ReplyPart(_HELP[lang])], intent

    if intent == "profile_info":
        return _match_step(state, _extract(text, p), intent)

    if intent in ("ask_odds", "set_list", "ask_plan") and not state.matches:
        return state, [ReplyPart(scope_reply("intake", lang))], intent

    codes = p.intended_choices or [m.code for m in state.matches]

    if intent == "ask_odds":
        estimates = remote.estimate(p, codes) if remote else _estimate(p, codes)
        state = state.model_copy(update={"estimates": estimates, "stage": "odds"})
        return state, [ReplyPart(render.render_points(estimates, p))], intent

    if intent == "set_list":
        p = _extract(text, p)  # picks up doubts like "no sé si lo aceptaría"
        choices, doubtful = parse_school_list(text, [m.code for m in state.matches])
        choices = choices or [m.code for m in state.matches]
        p = p.model_copy(update={
            "intended_choices": choices,
            "would_not_accept": list(dict.fromkeys(p.would_not_accept + doubtful)),
        })
        warnings = remote.check_list(p, choices, today) if remote else _check_list(p, choices, today)
        state = state.model_copy(update={"profile": p, "warnings": warnings, "stage": "listing"})
        return state, [ReplyPart(render.render_warnings(warnings, p))], intent

    if intent == "ask_plan":
        choices, _ = parse_school_list(text, [m.code for m in state.matches])
        if choices:  # "solo las tres" trims the list before planning
            p = p.model_copy(update={"intended_choices": choices})
        plan_codes = p.intended_choices or codes
        plan = remote.build_plan(p, plan_codes, lang, today) if remote else _build_plan(p, plan_codes, lang, today)
        state = state.model_copy(update={"profile": p, "plan": plan, "stage": "plan"})
        return state, [ReplyPart(render.render_plan(plan, p), "review", packet_review(plan, p, plan_codes))], intent

    if intent == "card_selection":
        reply = respond_to_selection(parse_card_selection(text) or {}, state)
        if reply is None:
            return state, [ReplyPart(scope_reply(state.stage, lang))], intent
        if reply.profile_updates is not None:  # intake form submitted: merge answers, then match
            # B11: an optional street address becomes home_lat/home_lng (the address itself is dropped)
            return _match_step(state, apply_address(merge(p, reply.profile_updates), reply.address), intent)
        if reply.add_code:
            p = p.model_copy(update={
                "intended_choices": list(dict.fromkeys(p.intended_choices + [reply.add_code])),
            })
            state = state.model_copy(update={"profile": p})
        return state, [ReplyPart(reply.narration, reply.kind, reply.payload)], intent

    if intent == "ask_rules":  # C12: rule questions answered from the official brochure, with a page citation
        return state, [ReplyPart(render_brochure_answer(brochure_answer(text, lang), lang))], intent

    if intent == "help":
        return state, [ReplyPart(_HELP[lang])], intent

    return state, [ReplyPart(scope_reply(state.stage, lang))], intent


async def bounded_turn(
    state: SessionState, text: str, today: date | None = None,
    remote: "RemoteTools | None" = None, timeout: float | None = None,
) -> tuple[SessionState, list[ReplyPart], str]:
    """handle_turn in a worker thread with a deadline. Raises TimeoutError or the handler's error;
    on either, the caller must not write state (a late thread result is discarded by wait_for)."""
    return await asyncio.wait_for(
        asyncio.to_thread(handle_turn, state, text, today, remote), timeout or TURN_TIMEOUT_S
    )


# ---------------------------------------------------------------------------
# Agent handlers
# ---------------------------------------------------------------------------


def _load_state(ctx: Context, key: str) -> SessionState:
    raw = ctx.storage.get(key)
    if raw:
        try:
            return SessionState.model_validate_json(raw)
        except Exception:
            ctx.logger.warning("corrupt session state at %s; starting fresh", key)
    return SessionState()


async def _send_part(ctx: Context, sender: str, part: ReplyPart) -> None:
    """Send one reply unit: split long narration into text ChatMessages, attach any card to the last."""
    chunks = split_reply(part.narration) if part.narration else [part.narration]
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        if last and part.kind and part.payload is not None:
            await ctx.send(sender, card_message(chunk, part.kind, part.payload))  # text + card, or text if invalid
        else:
            await ctx.send(sender, ChatMessage(content=[TextContent(text=chunk)]))


async def _apologize(ctx: Context, sender: str, lang: str | None) -> None:
    try:
        await ctx.send(sender, ChatMessage(content=[TextContent(text=_pick(_SORRY, lang))]))
    except Exception:
        ctx.logger.exception("could not send apology to %s", sender)


@chat_proto.on_message(ChatMessage)
async def on_chat_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))  # ALWAYS first
    key = session_key(sender, ctx.session)
    idle_key = f"{key}:idle"
    lang: str | None = None
    try:
        text = msg.text().strip()
        clean = strip_mention(text)
        parts: list[ReplyPart] = []
        for item in msg.content:
            if isinstance(item, StartSessionContent):
                if ctx.storage.has(key):
                    ctx.storage.remove(key)  # fresh chat: reset without writing state
                if ctx.storage.has(idle_key):
                    ctx.storage.remove(idle_key)
                parts.append(ReplyPart(_pick(_WELCOME, detect_language(text) if text else None)))
            elif isinstance(item, EndSessionContent):
                ctx.logger.info("session ended by %s", sender)
        state = _load_state(ctx, key)
        lang = _lang(state.profile)
        intent = "none"
        send_reply = True
        idle = int(ctx.storage.get(idle_key) or 0)
        remote = None
        if text:
            prev_stage = state.stage
            remote = _build_remote(ctx)
            state, reply_parts, intent = await bounded_turn(state, text, remote=remote)
            lang = _lang(state.profile)
            send_reply, idle = guard_decision(prev_stage, state.stage, intent, idle)
            parts.extend(reply_parts)
            ctx.storage.set(key, state.model_dump_json())  # written ONLY after an in-time success
            ctx.storage.set(idle_key, idle)
        # Footer lists ONLY specialists that genuinely replied this turn; never on fallback.
        if remote and remote.consulted and parts and send_reply:
            last = parts[-1]
            parts[-1] = ReplyPart(f"{last.narration}\n\n{_footer(remote.consulted, lang)}", last.kind, last.payload)
        card = any(p.kind and p.payload is not None for p in parts)
        ctx.logger.info(
            "turn sender=%s session=%s key=%s intent=%s stage=%s idle=%s send=%s card=%s text=%r",
            sender, ctx.session, key, intent, state.stage, idle, send_reply, card,
            "<form>" if '"address"' in clean else clean[:80],  # B11: never log a street address
        )
        if parts and (send_reply or not text):
            for part in parts:
                await _send_part(ctx, sender, part)
        elif not send_reply:
            ctx.logger.info("suppressed reply to %s (idle=%s intent=%s); loop guard", sender, idle, intent)
    except TimeoutError:
        ctx.logger.warning("turn timed out (>%.0fs) for %s; state untouched", TURN_TIMEOUT_S, sender)
        await _apologize(ctx, sender, lang)
    except Exception:
        ctx.logger.exception("handler failed for %s from %s", msg.msg_id, sender)
        await _apologize(ctx, sender, lang)


@chat_proto.on_message(ChatAcknowledgement)
async def on_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.debug("ack from %s for %s", sender, msg.acknowledged_msg_id)


async def _log_startup(ctx: Context):
    llm = "on" if os.getenv("ASI1_API_KEY") else "off"
    ctx.logger.info("USE_STUBS=%s LLM=%s", os.getenv("USE_STUBS", "false"), llm)
    if use_stubs():
        ctx.logger.warning(
            "USE_STUBS is TRUE — serving FIXTURE data, not the real modules. Do NOT run the demo like this."
        )


class DeepHealth(Model):
    ok: bool       # True only when the real round-trip reached the points agent
    ms: float
    path: str      # "remote" (specialist answered) | "fallback" (it didn't)


async def _health_deep(ctx: Context) -> DeepHealth:
    """One real counselor→points round-trip over the local hop — proves the specialist path end to
    end (A5 ping.py checks path=='remote'). Awaits send_and_receive directly; this runs on the agent
    loop, not a worker thread, so no run_coroutine_threadsafe bridge is needed here."""
    profile = FamilyProfile(language="es", current_grade=5, applying_grade=6,
                            interests=["arts"], home_neighborhood="Boyle Heights")
    codes = [m.code for m in matcher.match(profile)]
    addr = os.getenv("POINTS_AGENT_ADDRESS", "").strip()
    start = time.monotonic()
    path = "fallback"
    if addr:
        try:
            resp, _status = await ctx.send_and_receive(
                addr,
                PointsRequest(session_id="health", profile_json=profile.model_dump_json(), codes=codes),
                response_type=PointsResponse, timeout=int(REMOTE_TIMEOUT_S),
            )
            if resp is not None:
                path = "remote"
        except Exception:
            ctx.logger.exception("health/deep round-trip failed")
    return DeepHealth(ok=(path == "remote"), ms=round((time.monotonic() - start) * 1000, 1), path=path)


def build_agent() -> Agent:
    """Construct the agent at run time. Importing this module must never exit (issue #3):
    the seed is only required to run, not to import handlers for tests."""
    seed = os.getenv("COUNSELOR_SEED", "")
    if not seed:
        raise SystemExit("COUNSELOR_SEED is empty: set it in .env before running the counselor agent.")
    # A4b: resolve the two specialists to their local endpoints; everything else via the default resolver.
    rules = {}
    for addr, role in ((os.getenv("POINTS_AGENT_ADDRESS", "").strip(), "points"),
                       (os.getenv("PAPERWORK_AGENT_ADDRESS", "").strip(), "paperwork")):
        if addr:
            rules[addr] = local_url(role)
    agent = Agent(name="choices-counselor", seed=seed, mailbox=True, publish_agent_details=True,
                  port=int(os.getenv("COUNSELOR_PORT", "8000")), resolve=LocalFirst(rules))
    agent.include(chat_proto, publish_manifest=True)
    register_health(agent)  # A4b: our own /health keeps the local server up (don't rely on web_api)
    agent.on_rest_get("/health/deep", DeepHealth)(_health_deep)  # A5: real points round-trip for ping.py
    register_self_check(agent, "counselor")
    from agents.web_api import register_web  # POST /chat: replay_golden.py --live talks to this agent
    register_web(agent)
    agent.on_event("startup")(_log_startup)
    return agent


def main() -> None:
    build_agent().run()


if __name__ == "__main__":
    main()
