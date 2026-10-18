"""choices-points: wraps core/points + core/guardrails behind PointsRequest/PointsResponse.

Owner: Dev A, ticket A4 (#5). Must match the signature in core/stubs.py where one is listed.

A4: a mailbox uAgent the orchestrator calls over PointsRequest; it computes magnet points and
list guardrails (deterministic, no LLM) and replies with PointsResponse. On any compute error it
sends nothing, so the orchestrator falls back to its in-process core/ call rather than acting on a
blank answer.

Run: .venv/bin/python agents/points_agent.py   (needs POINTS_SEED in .env)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from agents.local_resolver import LocalFirst, local_url, register_health, register_self_check
from agents.messages import PointsRequest, PointsResponse
from core import guardrails, points
from core.types import FamilyProfile

load_dotenv()

points_proto = Protocol(name="choices-points", version="1.0")


@points_proto.on_message(PointsRequest, replies=PointsResponse)
async def handle_points(ctx: Context, sender: str, msg: PointsRequest):
    try:
        profile = FamilyProfile.model_validate_json(msg.profile_json)
        estimates = points.estimate(profile, msg.codes)
        warnings = guardrails.check_list(profile, msg.codes, date.today())
    except Exception:
        ctx.logger.exception("PointsRequest failed; sending nothing so the counselor falls back")
        return
    await ctx.send(sender, PointsResponse(
        session_id=msg.session_id,
        estimates_json=json.dumps([e.model_dump(mode="json") for e in estimates], ensure_ascii=False),
        warnings_json=json.dumps([w.model_dump(mode="json") for w in warnings], ensure_ascii=False),
    ))
    ctx.logger.info("PointsResponse to %s codes=%d", sender[:20], len(msg.codes))


async def _log_startup(ctx: Context):
    ctx.logger.info("choices-points USE_STUBS=%s addr=%s", os.getenv("USE_STUBS", "false"), ctx.agent.address)


def build_agent() -> Agent:
    seed = os.getenv("POINTS_SEED", "")
    if not seed:
        raise SystemExit("POINTS_SEED is empty: set it in .env before running the points agent.")
    port = int(os.getenv("POINTS_PORT", "8001"))  # orchestrator holds 8000
    counselor = os.getenv("COUNSELOR_AGENT_ADDRESS", "").strip()
    rules = {counselor: local_url("counselor")} if counselor else {}
    agent = Agent(name="choices-points", seed=seed, port=port, mailbox=True,
                  publish_agent_details=True, resolve=LocalFirst(rules))
    agent.include(points_proto, publish_manifest=True)
    register_health(agent)  # A4b: keeps the local server up for the counselor's local reply leg
    register_self_check(agent, "points")
    agent.on_event("startup")(_log_startup)
    return agent


def main() -> None:
    build_agent().run()


if __name__ == "__main__":
    main()
