"""choices-paperwork: wraps core/paperwork behind PaperworkRequest/PaperworkResponse.

Owner: Dev A, ticket A4 (#5). Must match the signature in core/stubs.py where one is listed.

A4: a mailbox uAgent the orchestrator calls over PaperworkRequest; it builds the action plan
(checklist, deadlines, steps, coordinator email) and replies with PaperworkResponse. The only LLM
is the email draft (already bounded + cached). On any error it sends nothing, so the orchestrator
falls back to its in-process core/ call.

Run: .venv/bin/python agents/paperwork_agent.py   (needs PAPERWORK_SEED in .env)
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from agents.messages import PaperworkRequest, PaperworkResponse
from core import paperwork
from core.types import FamilyProfile

load_dotenv()

paperwork_proto = Protocol(name="choices-paperwork", version="1.0")


@paperwork_proto.on_message(PaperworkRequest, replies=PaperworkResponse)
async def handle_paperwork(ctx: Context, sender: str, msg: PaperworkRequest):
    try:
        profile = FamilyProfile.model_validate_json(msg.profile_json)
        plan = paperwork.build_plan(profile, msg.codes, msg.language, date.today())
    except Exception:
        ctx.logger.exception("PaperworkRequest failed; sending nothing so the counselor falls back")
        return
    await ctx.send(sender, PaperworkResponse(session_id=msg.session_id, plan_json=plan.model_dump_json()))
    ctx.logger.info("PaperworkResponse to %s codes=%d lang=%s", sender[:20], len(msg.codes), msg.language)


async def _log_startup(ctx: Context):
    ctx.logger.info("choices-paperwork USE_STUBS=%s addr=%s", os.getenv("USE_STUBS", "false"), ctx.agent.address)


def build_agent() -> Agent:
    seed = os.getenv("PAPERWORK_SEED", "")
    if not seed:
        raise SystemExit("PAPERWORK_SEED is empty: set it in .env before running the paperwork agent.")
    port = int(os.getenv("PAPERWORK_PORT", "8002"))  # orchestrator 8000, points 8001
    agent = Agent(name="choices-paperwork", seed=seed, port=port, mailbox=True, publish_agent_details=True)
    agent.include(paperwork_proto, publish_manifest=True)
    agent.on_event("startup")(_log_startup)
    return agent


def main() -> None:
    build_agent().run()


if __name__ == "__main__":
    main()
