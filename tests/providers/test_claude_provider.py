"""Tests for ClaudeProvider: call() and check_status()."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from yoto_lib.providers.base import ProviderStatus


def _make_popen(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a fake Popen whose communicate() returns (stdout, stderr)."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


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
        with patch("yoto_lib.billing.costs.get_tracker", return_value=_noop_tracker()):
            yield

    def test_successful_call(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        payload = json.dumps({"animals": ["cat", "dog"]})
        proc = _make_popen(stdout=_wrap_result(payload))

        with patch("subprocess.Popen", return_value=proc):
            result = ClaudeProvider().call("list some animals", extract_json=False)

        assert result == payload

    def test_timeout_returns_none(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        proc = MagicMock()
        # communicate() raises on the first (timed-out) call, then returns after kill().
        proc.communicate.side_effect = [subprocess.TimeoutExpired(cmd="claude", timeout=120), ("", "")]

        with patch("subprocess.Popen", return_value=proc):
            result = ClaudeProvider().call("anything")

        assert result is None
        proc.kill.assert_called_once()

    def test_nonzero_exit_returns_none(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        proc = _make_popen(returncode=1, stdout="")
        with patch("subprocess.Popen", return_value=proc):
            result = ClaudeProvider().call("anything")

        assert result is None

    def test_extract_json_strips_fences(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        inner = '{"key": "value"}'
        fenced = f"```json\n{inner}\n```"
        proc = _make_popen(stdout=_wrap_result(fenced))

        with patch("subprocess.Popen", return_value=proc):
            result = ClaudeProvider().call("give me json")

        assert result == inner

    def test_extract_json_false_returns_raw(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        inner = '{"key": "value"}'
        fenced = f"```json\n{inner}\n```"
        proc = _make_popen(stdout=_wrap_result(fenced))

        with patch("subprocess.Popen", return_value=proc):
            result = ClaudeProvider().call("give me json", extract_json=False)

        # When extract_json=False the fences should NOT be stripped
        assert result == fenced

    def test_pre_cancelled_token_skips_subprocess(self):
        from yoto_lib.cancel import CancelToken
        from yoto_lib.providers.claude_provider import ClaudeProvider

        token = CancelToken()
        token.cancel()
        with patch("subprocess.Popen") as mock_popen:
            result = ClaudeProvider().call("anything", cancel_token=token)

        assert result is None
        mock_popen.assert_not_called()

    def test_cancel_during_call_returns_none(self):
        from yoto_lib.cancel import CancelToken
        from yoto_lib.providers.claude_provider import ClaudeProvider

        token = CancelToken()
        proc = _make_popen(stdout=_wrap_result("ignored"))

        # Simulate cancellation arriving while the subprocess is in flight: the
        # token is cancelled by the time communicate() returns.
        def _cancel_then_return(*_args, **_kwargs):
            token.cancel()
            return ("", "")

        proc.communicate.side_effect = _cancel_then_return

        with patch("subprocess.Popen", return_value=proc):
            result = ClaudeProvider().call("anything", cancel_token=token)

        assert result is None
        proc.terminate.assert_called_once()  # the registered proc was terminated


# ── ClaudeProvider.check_status ───────────────────────────────────────────────


class TestClaudeProviderCheckStatus:
    def test_cli_not_on_path(self):
        from yoto_lib.providers.claude_provider import ClaudeProvider

        with patch("shutil.which", return_value=None):
            status = ClaudeProvider.check_status()

        assert status.healthy is False
        assert status.message == "Claude CLI not found on PATH"

    def test_delegates_to_statuspage_when_cli_found(self):
        from yoto_lib.providers.base import _cache, _lock
        from yoto_lib.providers.claude_provider import ClaudeProvider

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
