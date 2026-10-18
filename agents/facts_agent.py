"""choices-facts: whole-school context from CDE (enrollment, English learners, students with IEPs).

Owner: Dev B, ticket B14 (#48). Same shape as choices-points (A4 pattern): a mailbox uAgent that answers
FactsRequest from core/school_facts.py (the prebuilt CDE file, no network, no LLM). On any error it sends
nothing, so the counselor falls back to its in-process core/school_facts call. Context only: never a
ranking or a quality judgment.

Run: .venv/bin/python agents/facts_agent.py   (needs FACTS_SEED in .env; port FACTS_PORT, default 8004)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from agents.local_resolver import LocalFirst, local_url, register_health, register_self_check
from agents.messages import FactsRequest, FactsResponse
from core.school_facts import facts

load_dotenv()

facts_proto = Protocol(name="choices-facts", version="1.0")


def answer(msg: FactsRequest) -> FactsResponse:
    return FactsResponse(session_id=msg.session_id,
                         facts_json=json.dumps({code: facts(code) for code in msg.codes}, ensure_ascii=False))


@facts_proto.on_message(FactsRequest, replies=FactsResponse)
async def handle_facts(ctx: Context, sender: str, msg: FactsRequest):
    try:
        reply = answer(msg)
    except Exception:
        ctx.logger.exception("FactsRequest failed; sending nothing so the counselor falls back")
        return
    await ctx.send(sender, reply)
    ctx.logger.info("FactsResponse to %s codes=%d", sender[:20], len(msg.codes))


def build_agent() -> Agent:
    seed = os.getenv("FACTS_SEED", "")
    if not seed:
        raise SystemExit("FACTS_SEED is empty: set it in .env before running the facts agent.")
    counselor = os.getenv("COUNSELOR_AGENT_ADDRESS", "").strip()
    rules = {counselor: local_url("counselor")} if counselor else {}
    agent = Agent(name="choices-facts", seed=seed, port=int(os.getenv("FACTS_PORT", "8004")), mailbox=True,
                  publish_agent_details=True, resolve=LocalFirst(rules))
    agent.include(facts_proto, publish_manifest=True)
    register_health(agent)
    register_self_check(agent, "facts")
    return agent


if __name__ == "__main__":
    build_agent().run()
