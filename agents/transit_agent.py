"""choices-transit: direct LA Metro bus lines from home to each school, behind TransitRequest/TransitResponse.

Owner: Dev B, ticket B13 (#47). Same shape as choices-points (A4 pattern): a mailbox uAgent that answers
from core/transit.py (the prebuilt GTFS index, no network, no LLM). On any error it sends nothing, so the
counselor falls back to its in-process core/transit call. The matcher already fills SchoolMatch.transit
locally; this agent is the same answer as a separate Fetch specialist for Dev A to register and hand off to.

Run: .venv/bin/python agents/transit_agent.py   (needs TRANSIT_SEED in .env; port TRANSIT_PORT, default 8003)
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
from agents.messages import TransitRequest, TransitResponse
from core.transit import transit_options
from core.types import FamilyProfile

load_dotenv()

transit_proto = Protocol(name="choices-transit", version="1.0")


def answer(msg: TransitRequest) -> TransitResponse:
    profile = FamilyProfile.model_validate_json(msg.profile_json)
    options = {code: [o.model_dump(mode="json") for o in transit_options(profile, code)] for code in msg.codes}
    return TransitResponse(session_id=msg.session_id, options_json=json.dumps(options, ensure_ascii=False))


@transit_proto.on_message(TransitRequest, replies=TransitResponse)
async def handle_transit(ctx: Context, sender: str, msg: TransitRequest):
    try:
        reply = answer(msg)
    except Exception:
        ctx.logger.exception("TransitRequest failed; sending nothing so the counselor falls back")
        return
    await ctx.send(sender, reply)
    ctx.logger.info("TransitResponse to %s codes=%d", sender[:20], len(msg.codes))


def build_agent() -> Agent:
    seed = os.getenv("TRANSIT_SEED", "")
    if not seed:
        raise SystemExit("TRANSIT_SEED is empty: set it in .env before running the transit agent.")
    counselor = os.getenv("COUNSELOR_AGENT_ADDRESS", "").strip()
    rules = {counselor: local_url("counselor")} if counselor else {}
    agent = Agent(name="choices-transit", seed=seed, port=int(os.getenv("TRANSIT_PORT", "8003")), mailbox=True,
                  publish_agent_details=True, resolve=LocalFirst(rules))
    agent.include(transit_proto, publish_manifest=True)
    register_health(agent)
    register_self_check(agent, "transit")
    return agent


if __name__ == "__main__":
    build_agent().run()
