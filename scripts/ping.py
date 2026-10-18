#!/usr/bin/env python3
"""A5 health check for the always-on host. Owner: Dev A, ticket A5 (#6).

Checks GET /health on all three agents (8000/8001/8002) and GET /health/deep on the counselor,
which runs one real counselor→points round-trip. Exit 0 only if every /health is ok AND the deep
round-trip's path is "remote" (the specialist actually answered, not a fallback).

    python scripts/ping.py        # exit 0 = healthy, nonzero = something is down

Follow-up (noted in the A5 PR): a send(text, session) -> str helper for replay_golden --live.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

AGENTS = (("counselor", 8000), ("points", 8001), ("paperwork", 8002))


def _get(url: str, timeout: float) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - localhost only
        return resp.status, json.loads(resp.read().decode())


def main() -> int:
    ok = True

    for role, port in AGENTS:
        try:
            status, body = _get(f"http://127.0.0.1:{port}/health", timeout=5)
            good = status == 200 and body.get("ok") is True
        except Exception as exc:  # noqa: BLE001
            status, body, good = "ERR", str(exc), False
        ok &= good
        print(f"/health {role:10s}:{port}  {status}  {body}  {'OK' if good else 'FAIL'}")

    try:
        status, body = _get("http://127.0.0.1:8000/health/deep", timeout=15)
        good = status == 200 and body.get("path") == "remote"
        note = "" if good else "  <-- path != remote (points unreachable / fell back)"
    except Exception as exc:  # noqa: BLE001
        status, body, good, note = "ERR", str(exc), False, ""
    ok &= good
    print(f"/health/deep            {status}  {body}  {'OK' if good else 'FAIL'}{note}")

    print("\nPING OK" if ok else "\nPING FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
