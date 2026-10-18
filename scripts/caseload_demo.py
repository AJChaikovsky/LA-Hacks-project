"""B12 handshake demo: a local partner agent buys a caseload report from choices-caseload on testnet.

Owner: Dev B, ticket B12 (#39). Record this once for the Fetch video (C7). Never in the live demo path.

    .venv/bin/python scripts/caseload_demo.py

Needs CASELOAD_SEED and BUYER_SEED in .env, and a buyer wallet with testnet FET (the script asks the
testnet faucet when it is low). Writes the report to docs/evidence/B12-caseload-report.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cosmpy.crypto.address import Address
from dotenv import load_dotenv
from uagents import Agent, Bureau, Context, Protocol
from uagents.setup import fund_agent_if_low
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from agents.caseload_agent import CaseloadReport, CaseloadRequest, create_agent
from core.config import load_config
from core.types import FamilyProfile

load_dotenv(ROOT / ".env")
CFG = load_config("caseload")
BUDGET_FET = Decimal("1")          # the partner agent refuses anything above this
OUT = ROOT / "docs" / "evidence" / "B12-caseload-report.json"

FAMILIES = [
    FamilyProfile.model_validate_json((ROOT / "tests" / "fixtures" / "maria_profile.json").read_text(encoding="utf-8")),
    FamilyProfile(language="en", child_name="Ana", current_grade=5, interests=["stem"], home_neighborhood="Sylmar"),
    FamilyProfile(language="en", child_name="Leo", current_grade=5, interests=["arts"], home_neighborhood="Pasadena"),
]


def main() -> None:
    seller = create_agent(os.environ["CASELOAD_SEED"])
    buyer = Agent(name="caseload-partner", seed=os.environ["BUYER_SEED"], network=CFG["network"])
    fund_agent_if_low(str(buyer.wallet.address()), min_balance=10**18)
    pay = Protocol(spec=payment_protocol_spec, role="buyer")

    @buyer.on_event("startup")
    async def ask(ctx: Context):
        ctx.logger.info(f"[PARTNER] CaseloadRequest for {len(FAMILIES)} families -> choices-caseload")
        await ctx.send(seller.address, CaseloadRequest(
            profiles_json=json.dumps([f.model_dump(mode="json") for f in FAMILIES])))

    @pay.on_message(RequestPayment)
    async def on_request_payment(ctx: Context, sender: str, msg: RequestPayment):
        funds = next((f for f in msg.accepted_funds if f.currency == "FET"), None)
        if funds is None or Decimal(funds.amount) > BUDGET_FET:
            await ctx.send(sender, RejectPayment(reason="over budget or unsupported currency"))
            return
        atto = int(Decimal(funds.amount) * CFG["atto_per_fet"])
        ctx.logger.info(f"[PARTNER] paying {funds.amount} testnet FET to {msg.recipient[:14]}…")
        tx = ctx.ledger.send_tokens(Address(msg.recipient), atto, CFG["denom"], buyer.wallet)
        await asyncio.to_thread(tx.wait_to_complete)
        ctx.logger.info(f"[PARTNER] CommitPayment tx={tx.tx_hash}")
        await ctx.send(sender, CommitPayment(funds=funds, recipient=msg.recipient, transaction_id=tx.tx_hash,
                                             reference=msg.reference, description="caseload report"))

    @pay.on_message(CompletePayment)
    async def on_complete(ctx: Context, sender: str, msg: CompletePayment):
        ctx.logger.info(f"[PARTNER] CompletePayment tx={msg.transaction_id[:12]}…")

    @pay.on_message(CancelPayment)
    async def on_cancel(ctx: Context, sender: str, msg: CancelPayment):
        ctx.logger.info(f"[PARTNER] CancelPayment: {msg.reason}")
        os._exit(1)

    @buyer.on_message(CaseloadReport)
    async def on_report(ctx: Context, sender: str, msg: CaseloadReport):
        report = json.loads(msg.report_json)
        OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for fam in report.get("families", []):
            pts = fam["points"]
            pts_text = (f"{'probably ' if pts['probably'] else ''}{pts['total']} of {pts['max']} points"
                        + (f" (unknown: {', '.join(pts['unknown_factors'])})" if pts["unknown_factors"] else "")
                        if pts else "no magnet points")
            schools = ", ".join(f"{m['name']} {m['theme']}" for m in fam["matches"])
            warn = fam["top_warning"]["code"] if fam["top_warning"] else "none"
            ctx.logger.info(f"[REPORT] {fam['child']}: {schools} | {pts_text} | top warning: {warn}")
        ctx.logger.info(f"[REPORT] saved {OUT.relative_to(ROOT)} (paid {report.get('paid', {}).get('fet')} FET)")
        os._exit(0)

    buyer.include(pay)
    bureau = Bureau(agents=[seller, buyer], port=int(os.getenv("CASELOAD_DEMO_PORT", "8020")), network="testnet")
    bureau.run()


if __name__ == "__main__":
    main()
