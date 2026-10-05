"""Tests run the same on every machine: the user's own DESK_PLAN (loaded from their settings file
at import) must not decide which path a test takes. A test that wants ChatGPT sets it itself."""

import pytest


@pytest.fixture(autouse=True)
def _claude_plan_by_default(monkeypatch):
    monkeypatch.setenv("DESK_PLAN", "claude")
