"""Tests for Claude provider edge cases."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from yoto_lib.providers.claude_provider import ClaudeProvider, _extract_json


class TestExtractJson:
    def test_strips_json_code_fence(self):
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json(text) == '{"key": "value"}'

    def test_strips_plain_code_fence(self):
        text = '```\n["a", "b"]\n```'
        assert _extract_json(text) == '["a", "b"]'

    def test_no_code_fence_returns_stripped(self):
        text = '  {"key": "value"}  '
        assert _extract_json(text) == '{"key": "value"}'

    def test_empty_string(self):
        assert _extract_json("") == ""

    def test_code_fence_with_surrounding_text(self):
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone!'
        assert _extract_json(text) == '{"a": 1}'


class TestClaudeProviderCheckStatus:
    def test_cli_not_found_returns_unhealthy(self):
        """When claude CLI is not on PATH, returns unhealthy."""
        with patch("shutil.which", return_value=None):
            status = ClaudeProvider.check_status()
        assert status.healthy is False
        assert "CLI not found" in status.message

    def test_cli_found_checks_status_page(self):
        """When claude CLI exists, checks the status page."""
        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch("yoto_lib.providers.base._cache", {}),
            patch("yoto_lib.providers.base.httpx.get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "status": {"indicator": "none"},
            }
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            status = ClaudeProvider.check_status()

        assert status.healthy is True


def _make_popen(returncode: int = 0, stdout: str = "") -> MagicMock:
    """Build a fake Popen whose communicate() returns (stdout, "")."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, "")
    return proc


class TestClaudeProviderCall:
    def test_returns_none_on_cli_not_found(self):
        """Returns None when claude CLI is not installed."""
        provider = ClaudeProvider()
        with patch(
            "yoto_lib.providers.claude_provider.subprocess.Popen",
            side_effect=FileNotFoundError("claude"),
        ):
            result = provider.call("test prompt")
        assert result is None

    def test_returns_none_on_timeout(self):
        """Returns None when CLI times out."""
        import subprocess

        provider = ClaudeProvider()
        proc = MagicMock()
        proc.communicate.side_effect = [subprocess.TimeoutExpired("claude", 120), ("", "")]
        with patch(
            "yoto_lib.providers.claude_provider.subprocess.Popen",
            return_value=proc,
        ):
            result = provider.call("test prompt")
        assert result is None

    def test_returns_none_on_nonzero_exit(self):
        """Returns None when CLI exits with non-zero status."""
        provider = ClaudeProvider()
        with patch(
            "yoto_lib.providers.claude_provider.subprocess.Popen",
            return_value=_make_popen(returncode=1, stdout=""),
        ):
            result = provider.call("test prompt")
        assert result is None

    def test_returns_none_on_error_response(self):
        """Returns None when Claude's JSON response has is_error=True."""
        provider = ClaudeProvider()
        stdout = json.dumps({"is_error": True, "result": "Error occurred"})
        with patch(
            "yoto_lib.providers.claude_provider.subprocess.Popen",
            return_value=_make_popen(returncode=0, stdout=stdout),
        ):
            result = provider.call("test prompt")
        assert result is None

    def test_successful_call_returns_text(self):
        """Returns extracted text on successful call."""
        provider = ClaudeProvider()
        stdout = json.dumps({"result": '```json\n{"answer": 42}\n```'})
        with (
            patch(
                "yoto_lib.providers.claude_provider.subprocess.Popen",
                return_value=_make_popen(returncode=0, stdout=stdout),
            ),
            patch("yoto_lib.billing.costs.get_tracker") as mock_tracker,
        ):
            mock_tracker.return_value.record = MagicMock()
            result = provider.call("test prompt")
        assert result == '{"answer": 42}'

    def test_extract_json_disabled(self):
        """With extract_json=False, returns raw text without stripping fences."""
        provider = ClaudeProvider()
        stdout = json.dumps({"result": "  plain text  "})
        with (
            patch(
                "yoto_lib.providers.claude_provider.subprocess.Popen",
                return_value=_make_popen(returncode=0, stdout=stdout),
            ),
            patch("yoto_lib.billing.costs.get_tracker") as mock_tracker,
        ):
            mock_tracker.return_value.record = MagicMock()
            result = provider.call("test prompt", extract_json=False)
        assert result == "plain text"
