"""Tests never call ASI:One unless a test opts in (RUN_LIVE=1 tests, or monkeypatching)."""
import os

os.environ.setdefault("EMAIL_LLM", "off")
os.environ.setdefault("DEMO_MODE", "false")
