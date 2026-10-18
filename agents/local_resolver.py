"""LocalFirst resolver + a /health REST handler so co-located agents talk over localhost.

Owner: Dev A, ticket A4b (#5 follow-up).

The three agents run on one host (A5). LocalFirst maps the sibling agents' addresses to their
local http://127.0.0.1:<port>/submit endpoints, so the orchestrator↔specialist hop is direct
localhost HTTP (~1ms) instead of the Agentverse mailbox relay (~2-4s, and drifting). Every address
NOT in the rules falls through to the normal GlobalResolver, so mailbox stays intact for ASI:One
senders. Each agent keeps mailbox=True; a trivial GET /health keeps its local server running
(a mailbox agent drops its server unless it has a REST handler — uagents agent.py:1293).

EXTRA_LOCAL_RULES="addr=url,addr=url" is a test-only hook used by the de-risk round-trips.
"""
from __future__ import annotations

import os

from uagents import Context, Model
from uagents.resolver import GlobalResolver, Resolver, RulesBasedResolver

_PORTS = {"counselor": "COUNSELOR_PORT", "points": "POINTS_PORT", "paperwork": "PAPERWORK_PORT", "transit": "TRANSIT_PORT", "facts": "FACTS_PORT"}
_DEFAULT_PORT = {"counselor": "8000", "points": "8001", "paperwork": "8002", "transit": "8003", "facts": "8004"}


def local_url(role: str) -> str:
    port = os.getenv(_PORTS[role], _DEFAULT_PORT[role])
    return f"http://127.0.0.1:{port}/submit"


def _env_rules() -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in os.getenv("EXTRA_LOCAL_RULES", "").split(","):
        if "=" in pair:
            addr, url = pair.split("=", 1)
            if addr.strip() and url.strip():
                out[addr.strip()] = url.strip()
    return out


class LocalFirst(Resolver):
    """Resolve rule addresses to local endpoints; delegate everything else to the fallback."""

    def __init__(self, rules: dict[str, str], fallback: Resolver | None = None):
        merged = {k: v for k, v in {**rules, **_env_rules()}.items() if k and v}
        self._rules = RulesBasedResolver(merged)
        self._fallback = fallback or GlobalResolver()

    async def resolve(self, destination: str):
        addr, endpoints = await self._rules.resolve(destination)
        if endpoints:
            return addr, endpoints
        return await self._fallback.resolve(destination)


class Health(Model):
    ok: bool


def register_health(agent) -> None:
    """A GET /health handler; also keeps a mailbox agent's local server alive for /submit."""

    async def _health(ctx: Context) -> Health:
        return Health(ok=True)

    agent.on_rest_get("/health", Health)(_health)


def register_self_check(agent, role: str) -> None:
    """On startup, probe this agent's own local /health. If unreachable, the local server isn't
    serving /submit — sibling agents would silently fall back — so log a loud WARNING."""

    async def _self_check(ctx: Context):
        import asyncio
        import httpx

        url = f"http://127.0.0.1:{os.getenv(_PORTS[role], _DEFAULT_PORT[role])}/health"
        await asyncio.sleep(4)  # let the ASGI server bind
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(url, timeout=3)
            if resp.status_code == 200:
                ctx.logger.info("SELF-CHECK ok: local server serving at %s (local /submit reachable)", url)
            else:
                ctx.logger.warning(
                    "SELF-CHECK: %s returned HTTP %s — local /submit may be unreachable; "
                    "sibling hops will fall back", url, resp.status_code,
                )
        except Exception as exc:  # noqa: BLE001
            ctx.logger.warning(
                "SELF-CHECK FAILED: local server not reachable at %s (%s) — the local-endpoint hop "
                "will fail and fall back", url, exc,
            )

    agent.on_event("startup")(_self_check)
