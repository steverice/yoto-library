"""Tests for cost tracking edge cases and utility functions."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from yoto_lib.billing.costs import CostTracker, get_tracker, reset_tracker
from yoto_lib.providers.claude_provider import ClaudeProvider


class TestIsSubscription:
    def test_claude_without_api_key_is_subscription(self):
        with patch.dict(os.environ, {}, clear=True):
            assert ClaudeProvider().is_subscription is True

    def test_claude_with_api_key_is_not_subscription(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            assert ClaudeProvider().is_subscription is False

    def test_image_provider_is_not_subscription(self):
        from yoto_lib.providers.openai_provider import OpenAIProvider
        assert OpenAIProvider().is_subscription is False


class TestGetAndResetTracker:
    def test_get_tracker_returns_singleton(self):
        t1 = get_tracker()
        t2 = get_tracker()
        assert t1 is t2

    def test_reset_tracker_returns_new_instance(self):
        t1 = get_tracker()
        t1.record("retrodiffusion")
        assert t1.has_records()

        t2 = reset_tracker()
        assert t2 is not t1
        assert not t2.has_records()

    def test_get_tracker_after_reset_returns_new(self):
        old = get_tracker()
        reset_tracker()
        new = get_tracker()
        assert new is not old


class TestCostTrackerSummaryEdgeCases:
    def test_summary_subscription_only(self):
        t = CostTracker()
        t.record("claude_haiku", subscription=True)
        t.record("claude_sonnet", subscription=True)
        lines = t.summary_lines()
        assert len(lines) == 1
        assert "subscription" in lines[0]
        assert "Estimated cost" not in lines[0]

    def test_records_returns_empty_for_unknown_key(self):
        t = CostTracker()
        t.record("nonexistent_key")
        records = t.records()
        assert records == {}
