"""Tests for billing persistence and queries."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from yoto_lib.billing.costs import CostTracker


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
        t.record("openai_generate_low")
        persist_session(t)
        reset_totals()
        assert read_totals() == {}

    def test_reset_totals_provider_group(self, billing_file):
        from yoto_lib.billing import persist_session, read_totals, reset_totals

        t = CostTracker()
        t.record("retrodiffusion", count=3)
        t.record("openai_generate_low")
        t.record("openai_edit_medium")
        persist_session(t)
        reset_totals("openai")
        totals = read_totals()
        assert "retrodiffusion" in totals
        assert "openai_generate_low" not in totals
        assert "openai_edit_medium" not in totals


class TestFetchBalances:
    def test_retrodiffusion_balance(self):
        from yoto_lib.billing import fetch_balances

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "remaining_balance": 12.45,
            "balance_cost": 0.25,
            "output_images": [],
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch.dict("os.environ", {"RETRODIFFUSION_API_KEY": "test-key"}),
            patch("yoto_lib.billing.httpx.post", return_value=mock_response),
        ):
            balances = fetch_balances()

        assert balances["RetroDiffusion"]["balance"] == 12.45

    def test_retrodiffusion_not_configured(self):
        from yoto_lib.billing import fetch_balances

        with patch.dict("os.environ", {}, clear=True):
            balances = fetch_balances()
        assert "RetroDiffusion" not in balances

    def test_retrodiffusion_api_error(self):
        from yoto_lib.billing import fetch_balances

        with (
            patch.dict("os.environ", {"RETRODIFFUSION_API_KEY": "test-key"}),
            patch("yoto_lib.billing.httpx.post", side_effect=OSError("timeout")),
        ):
            balances = fetch_balances()
        assert balances["RetroDiffusion"]["error"] == "timeout"


class TestSubscriptionUsage:
    def test_claude_subscription_usage(self):
        from yoto_lib.billing import fetch_subscription_usage

        mock_keychain = MagicMock()
        mock_keychain.stdout = json.dumps({"claudeAiOauth": {"accessToken": "test-token"}})
        mock_keychain.returncode = 0
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "five_hour": {"utilization": 32.0, "resets_at": "2026-04-07T12:00:00Z"},
            "seven_day": {"utilization": 30.0, "resets_at": "2026-04-11T20:00:00Z"},
            "seven_day_sonnet": {"utilization": 3.0, "resets_at": "2026-04-11T20:00:00Z"},
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("yoto_lib.billing.subprocess.run", return_value=mock_keychain),
            patch("yoto_lib.billing.httpx.get", return_value=mock_response),
        ):
            usage = fetch_subscription_usage()

        assert usage["session"]["utilization"] == 32.0
        assert usage["weekly"]["utilization"] == 30.0
        assert usage["weekly_sonnet"]["utilization"] == 3.0

    def test_claude_no_keychain(self):
        from yoto_lib.billing import fetch_subscription_usage

        mock_keychain = MagicMock()
        mock_keychain.returncode = 44
        mock_keychain.stdout = ""

        with patch("yoto_lib.billing.subprocess.run", return_value=mock_keychain):
            usage = fetch_subscription_usage()

        assert usage is None


class TestBillingCommand:
    def test_billing_shows_lifetime_spend(self, billing_file):
        from yoto_cli.main import cli
        from yoto_lib.billing import persist_session

        # Simulate a session
        t = CostTracker()
        t.record("retrodiffusion", count=5)
        t.record("openai_generate_low", count=2)
        persist_session(t)

        runner = CliRunner()
        result = runner.invoke(cli, ["providers"])
        assert result.exit_code == 0
        assert "Lifetime spend" in result.output
        assert "RetroDiffusion" in result.output
        assert "OpenAI" in result.output

    def test_billing_reset_all(self, billing_file):
        from yoto_cli.main import cli
        from yoto_lib.billing import persist_session, read_totals

        t = CostTracker()
        t.record("retrodiffusion", count=3)
        persist_session(t)

        runner = CliRunner()
        result = runner.invoke(cli, ["providers", "--reset"], input="y\n")
        assert result.exit_code == 0
        assert "Reset all" in result.output
        assert read_totals() == {}

    def test_billing_reset_provider(self, billing_file):
        from yoto_cli.main import cli
        from yoto_lib.billing import persist_session, read_totals

        t = CostTracker()
        t.record("retrodiffusion", count=3)
        t.record("openai_generate_low")
        persist_session(t)

        runner = CliRunner()
        result = runner.invoke(cli, ["providers", "--reset", "openai"])
        assert result.exit_code == 0
        assert "Reset lifetime billing data for openai" in result.output
        totals = read_totals()
        assert "retrodiffusion" in totals
        assert "openai_generate_low" not in totals

    def test_billing_reset_invalid_provider(self, billing_file):
        from yoto_cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["providers", "--reset", "invalid"])
        assert result.exit_code != 0
        assert "Unknown provider group" in result.output
