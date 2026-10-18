"""Tests never call ASI:One unless a test opts in (RUN_LIVE=1 tests, or monkeypatching)."""
import os

os.environ.setdefault("EMAIL_LLM", "off")
os.environ.setdefault("DEMO_MODE", "false")
# Tests exercise the real modules even if a local .env still says USE_STUBS=true.
os.environ["USE_STUBS"] = "false"
# agents/orchestrator.py exits at import without a seed; tests import it but never run or register the agent.
os.environ.setdefault("COUNSELOR_SEED", "pytest-only-seed-never-registered")
