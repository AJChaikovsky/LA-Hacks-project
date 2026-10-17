"""Read-only loaders for config/*.json and data/*.json. Owner: Dev B (B2).

Every number the agent says comes from these files; nothing here is computed.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@cache
def load_config(name: str) -> dict:
    """load_config("points") -> parsed config/points.json."""
    return json.loads((ROOT / "config" / f"{name}.json").read_text(encoding="utf-8"))


@cache
def load_data(name: str):
    """load_data("schools") -> parsed data/schools.json."""
    return json.loads((ROOT / "data" / f"{name}.json").read_text(encoding="utf-8"))
