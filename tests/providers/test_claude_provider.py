"""Tests for ClaudeProvider: call() and check_status()."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from yoto_lib.providers.base import ProviderStatus


def _make_subprocess_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a fake CompletedProcess-like object."""
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


def _wrap_result(text: str, is_error: bool = False) -> str:
    """Return JSON string mimicking Claude CLI --output-format json output."""
    return json.dumps({"result": text, "is_error": is_error})


def _noop_tracker():
    """Return a CostTracker mock that accepts .record() calls silently."""
    tracker = MagicMock()
    tracker.record = MagicMock()
    return tracker


# ── ClaudeProvider.call ───────────────────────────────────────────────────────


class TestClaudeProviderCall:
    @pytest.fixture(autouse=True)
    def _patch_costs(self):
        """Suppress cost-tracking side effects in every test in this class."""
        with patch("yoto_lib.providers.claude_provider.ClaudeProvider.call.__wrapped__", None, create=True):
            pass
        with (
            patch("yoto_lib.billing.costs.get_tracker", return_value=_noop_tracker()),
            patch("yoto_lib.billing.costs.is_subscription", return_value=False),
        ):
            yield

    def test_successful_call(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        payload = json.dumps({"animals": ["cat", "dog"]})
        sp_result = _make_subprocess_result(stdout=_wrap_result(payload))

        with patch("subprocess.run", return_value=sp_result):
            result = ClaudeProvider().call("list some animals", extract_json=False)

        assert result == payload

    def test_timeout_returns_none(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120),
        ):
            result = ClaudeProvider().call("anything")

        assert result is None

    def test_nonzero_exit_returns_none(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        sp_result = _make_subprocess_result(returncode=1, stdout="")
        with patch("subprocess.run", return_value=sp_result):
            result = ClaudeProvider().call("anything")

        assert result is None

    def test_extract_json_strips_fences(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        inner = '{"key": "value"}'
        fenced = f"```json\n{inner}\n```"
        sp_result = _make_subprocess_result(stdout=_wrap_result(fenced))

        with patch("subprocess.run", return_value=sp_result):
            result = ClaudeProvider().call("give me json")

        assert result == inner

    def test_extract_json_false_returns_raw(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        inner = '{"key": "value"}'
        fenced = f"```json\n{inner}\n```"
        sp_result = _make_subprocess_result(stdout=_wrap_result(fenced))

        with patch("subprocess.run", return_value=sp_result):
            result = ClaudeProvider().call("give me json", extract_json=False)

        # When extract_json=False the fences should NOT be stripped
        assert result == fenced


# ── ClaudeProvider.check_status ───────────────────────────────────────────────


class TestClaudeProviderCheckStatus:
    def test_cli_not_on_path(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        with patch("shutil.which", return_value=None):
            status = ClaudeProvider.check_status()

        assert status.healthy is False
        assert status.message == "Claude CLI not found on PATH"

    def test_delegates_to_statuspage_when_cli_found(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider
        from yoto_lib.providers.base import _cache, _lock

        # Clear cache so _fetch_statuspage actually calls httpx
        with _lock:
            _cache.clear()

        healthy_status = ProviderStatus(healthy=True)

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch.object(ClaudeProvider, "_fetch_statuspage", return_value=healthy_status) as mock_fetch,
        ):
            status = ClaudeProvider.check_status()

        mock_fetch.assert_called_once_with(ClaudeProvider.status_page_url)
        assert status is healthy_status
