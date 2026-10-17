"""Agent-to-agent message models. FROZEN at gate G0 (add fields only, with defaults).

Payloads are JSON strings of core/types.py models so the uAgents Models stay flat and
serialization can't break between agents. Use `.model_dump_json()` to send and
`Model.model_validate_json()` to read.
"""
from uagents import Model


class PointsRequest(Model):
    session_id: str
    profile_json: str        # FamilyProfile
    codes: list[str]         # program codes in rank order


class PointsResponse(Model):
    session_id: str
    estimates_json: str      # list[PointsEstimate]
    warnings_json: str       # list[GuardrailWarning]


class PaperworkRequest(Model):
    session_id: str
    profile_json: str        # FamilyProfile
    codes: list[str]
    language: str            # "es" | "en"


class PaperworkResponse(Model):
    session_id: str
    plan_json: str           # ActionPlan
