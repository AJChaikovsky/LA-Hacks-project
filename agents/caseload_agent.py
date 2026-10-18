"""choices-caseload: paid batch reports for partner agents, over the Payment Protocol (seller role).

Owner: Dev B, ticket B12 (#39). Families always use choices-counselor for free; partner agents
(nonprofits, parent centers, counselors) send a CaseloadRequest with N family profiles and pay
0.1 testnet FET per family (config/caseload.json). Never in the live demo path.

Flow: CaseloadRequest -> RequestPayment -> (buyer) CommitPayment with a testnet tx hash -> the seller
verifies the transfer on the ledger -> CompletePayment + CaseloadReport. RejectPayment, a failed
check, or a missed deadline -> CancelPayment, politely.

Run standalone: .venv/bin/python agents/caseload_agent.py   (needs CASELOAD_SEED; CASELOAD_MAILBOX=true for Agentverse)
Local handshake demo: .venv/bin/python scripts/caseload_demo.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from uagents import Agent, Context, Model, Protocol
from uagents.network import wait_for_tx_to_complete
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from core.caseload import build_report, payment_matches, price
from core.config import load_config
from core.types import FamilyProfile


class CaseloadRequest(Model):
    profiles_json: str            # JSON list of core.types.FamilyProfile


class CaseloadReport(Model):
    reference: str
    report_json: str              # core.caseload.build_report(...) as JSON, or {"error": ...}


ABOUT = ("I'm choices-caseload. Families: talk to choices-counselor, it's free. Partner agents: send a "
         "CaseloadRequest with family profiles and I'll reply with a RequestPayment "
         "(0.1 testnet FET per family) and then a batch report: 3 matches, points and the top warning per family.")


def create_agent(seed: str, **agent_kwargs) -> Agent:
    cfg = load_config("caseload")
    agent_kwargs.setdefault("network", cfg["network"])  # uagents defaults to mainnet; payments are testnet only
    agent = Agent(name="choices-caseload", seed=seed, **agent_kwargs)
    wallet = str(agent.wallet.address())

    caseload = Protocol(name="ChoicesCaseload", version="0.1.0")
    payments = Protocol(spec=payment_protocol_spec, role="seller")
    chat = Protocol(spec=chat_protocol_spec)

    def pending(ctx: Context) -> dict:
        return ctx.storage.get("pending") or {}

    def save(ctx: Context, data: dict) -> None:
        ctx.storage.set("pending", data)

    @caseload.on_message(CaseloadRequest)
    async def on_request(ctx: Context, sender: str, msg: CaseloadRequest):
        try:
            profiles = [FamilyProfile.model_validate(p) for p in json.loads(msg.profiles_json)]
        except Exception:
            await ctx.send(sender, CaseloadReport(reference="", report_json=json.dumps({"error": "invalid profiles"})))
            return
        if not 1 <= len(profiles) <= cfg["max_families"]:
            error = f"send between 1 and {cfg['max_families']} family profiles"
            await ctx.send(sender, CaseloadReport(reference="", report_json=json.dumps({"error": error})))
            return
        fet, atto = price(len(profiles))
        ref = uuid.uuid4().hex
        data = pending(ctx)
        data[ref] = {"sender": sender, "profiles_json": msg.profiles_json, "atto": atto, "fet": fet,
                     "expires": time.time() + cfg["deadline_seconds"]}
        save(ctx, data)
        ctx.logger.info(f"[PAYMENT] RequestPayment {fet} FET for {len(profiles)} families ref={ref[:8]}")
        await ctx.send(sender, RequestPayment(
            accepted_funds=[Funds(amount=fet, currency="FET", payment_method="fet_direct")],
            recipient=wallet, deadline_seconds=cfg["deadline_seconds"], reference=ref,
            description=f"Choices caseload report for {len(profiles)} families (testnet; families always free)",
        ))

    @payments.on_message(CommitPayment)
    async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
        data = pending(ctx)
        job = data.get(msg.reference)
        if not job or job["sender"] != sender:
            await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason="unknown or expired request"))
            return
        try:
            tx = await wait_for_tx_to_complete(msg.transaction_id, ctx.ledger)
            events = [tx.events, *(log.events for log in tx.logs)]
            paid = tx.is_successful() and payment_matches(events, wallet, job["atto"], cfg["denom"])
        except Exception as exc:
            ctx.logger.warning(f"[PAYMENT] could not verify tx: {type(exc).__name__}")
            paid = False
        if not paid:
            await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id,
                                                 reason="payment not found on testnet for this request"))
            return
        profiles = [FamilyProfile.model_validate(p) for p in json.loads(job["profiles_json"])]
        report = build_report(profiles)
        report["paid"] = {"fet": job["fet"], "network": cfg["network"], "tx": msg.transaction_id,
                          "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        data.pop(msg.reference, None)
        save(ctx, data)
        ctx.logger.info(f"[PAYMENT] verified {job['fet']} FET tx={msg.transaction_id[:12]}… -> CompletePayment + report")
        await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
        await ctx.send(sender, CaseloadReport(reference=msg.reference, report_json=json.dumps(report)))

    @payments.on_message(RejectPayment)
    async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
        data = pending(ctx)
        for ref in [r for r, j in data.items() if j["sender"] == sender]:
            data.pop(ref)
        save(ctx, data)
        ctx.logger.info(f"[PAYMENT] buyer declined: {msg.reason}. No charge, nothing sent.")

    @agent.on_interval(period=30.0)
    async def expire(ctx: Context):
        data, now = pending(ctx), time.time()
        for ref, job in [(r, j) for r, j in data.items() if j["expires"] < now]:
            data.pop(ref)
            await ctx.send(job["sender"], CancelPayment(transaction_id="", reason="payment deadline passed; nothing charged"))
        save(ctx, data)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc),
                                                   acknowledged_msg_id=msg.msg_id))
        await ctx.send(sender, ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid.uuid4(),
                                           content=[TextContent(type="text", text=ABOUT)]))

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    agent.include(caseload, publish_manifest=True)
    agent.include(payments, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    return agent


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    seed = os.getenv("CASELOAD_SEED", "")
    if not seed:
        raise SystemExit("CASELOAD_SEED is empty: set it in .env before running the caseload agent.")
    mailbox = os.getenv("CASELOAD_MAILBOX", "false").lower() == "true"
    kwargs = {"port": int(os.getenv("CASELOAD_PORT", "8010"))}  # never 8000: that's the counselor's web port
    if mailbox:
        kwargs |= {"mailbox": True, "publish_agent_details": True}
    create_agent(seed, **kwargs).run()
