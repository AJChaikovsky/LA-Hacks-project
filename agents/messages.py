"""Shared uagents Models between the orchestrator and specialists. FROZEN at G0.

Payloads are JSON strings (core.types models dumped with model_dump_json) to avoid
nested-model serialization bugs between agents.
"""
from uagents import Model


class PointsRequest(Model):
    session_id: str
    profile_json: str
    codes: list[str]


class PointsResponse(Model):
    session_id: str
    estimates_json: str
    warnings_json: str


class PaperworkRequest(Model):
    session_id: str
    profile_json: str
    codes: list[str]
    language: str


class PaperworkResponse(Model):
    session_id: str
    plan_json: str
