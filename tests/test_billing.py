"""Tests for billing persistence and queries."""

import json
from pathlib import Path

import pytest

from yoto_lib.costs import CostTracker


@pytest.fixture
def billing_file(tmp_path, monkeypatch):
    """Redirect billing storage to a temp file."""
    bf = tmp_path / "billing.json"
    monkeypatch.setattr("yoto_lib.billing.BILLING_FILE", bf)
    return bf


class TestPersistence:
    def test_persist_session_creates_file(self, billing_file):
        from yoto_lib.billing import persist_session
        t = CostTracker()
        t.record("retrodiffusion", count=3)
        persist_session(t)
        assert billing_file.exists()
        data = json.loads(billing_file.read_text())
        assert data["totals"]["retrodiffusion"]["cost"] == pytest.approx(0.009)
        assert data["totals"]["retrodiffusion"]["calls"] == 3

    def test_persist_session_accumulates(self, billing_file):
        from yoto_lib.billing import persist_session
        t1 = CostTracker()
        t1.record("retrodiffusion", count=2)
        persist_session(t1)

        t2 = CostTracker()
        t2.record("retrodiffusion", count=1)
        persist_session(t2)

        data = json.loads(billing_file.read_text())
        assert data["totals"]["retrodiffusion"]["cost"] == pytest.approx(0.009)
        assert data["totals"]["retrodiffusion"]["calls"] == 3

    def test_persist_session_subscription_zero_cost(self, billing_file):
        from yoto_lib.billing import persist_session
        t = CostTracker()
        t.record("claude_haiku", subscription=True)
        persist_session(t)
        data = json.loads(billing_file.read_text())
        assert data["totals"]["claude_haiku"]["cost"] == 0.0
        assert data["totals"]["claude_haiku"]["calls"] == 1

    def test_read_totals_empty(self, billing_file):
        from yoto_lib.billing import read_totals
        assert read_totals() == {}

    def test_read_totals_returns_data(self, billing_file):
        from yoto_lib.billing import persist_session, read_totals
        t = CostTracker()
        t.record("retrodiffusion", count=5)
        persist_session(t)
        totals = read_totals()
        assert totals["retrodiffusion"]["calls"] == 5

    def test_reset_totals_all(self, billing_file):
        from yoto_lib.billing import persist_session, read_totals, reset_totals
        t = CostTracker()
        t.record("retrodiffusion", count=3)
        t.record("openai_generate")
        persist_session(t)
        reset_totals()
        assert read_totals() == {}

    def test_reset_totals_provider_group(self, billing_file):
        from yoto_lib.billing import persist_session, read_totals, reset_totals
        t = CostTracker()
        t.record("retrodiffusion", count=3)
        t.record("openai_generate")
        t.record("openai_edit")
        persist_session(t)
        reset_totals("openai")
        totals = read_totals()
        assert "retrodiffusion" in totals
        assert "openai_generate" not in totals
        assert "openai_edit" not in totals
