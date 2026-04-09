"""Claude CLI provider — calls Claude via subprocess."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

from yoto_lib.providers.base import Provider, ProviderStatus, StatusPageMixin

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """Strip markdown code fences from Claude output to get raw JSON."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


class ClaudeProvider(StatusPageMixin, Provider):
    """Calls Claude CLI for text/vision tasks (matching, comparison, description)."""

    display_name = "Claude"
    status_page_url = "https://status.claude.com/api/v2/status.json"

    @classmethod
    def check_status(cls) -> ProviderStatus:
        if not shutil.which("claude"):
            return ProviderStatus(healthy=False, message="Claude CLI not found on PATH")
        return super().check_status()

    def call(
        self,
        prompt: str,
        *,
        allowed_tools: str = "",
        timeout: int = 120,
        model: str = "haiku",
        extract_json: bool = True,
    ) -> str | None:
        """Call Claude CLI and return the response text, or None on failure.

        Args:
            prompt: The prompt to send.
            allowed_tools: Comma-separated tool names (e.g. "Read"). Empty disables tools.
            timeout: Subprocess timeout in seconds.
            model: Claude model to use (haiku, sonnet, etc.)
            extract_json: If True, strip markdown code fences from the response.
        """
        cmd = [
            "claude", "-p", prompt,
            "--output-format", "json",
            "--model", model,
        ]
        if allowed_tools:
            cmd += ["--allowedTools", allowed_tools]
        else:
            cmd += ["--tools", ""]

        try:
            logger.debug("claude_provider.call: model=%s prompt_length=%d", model, len(prompt))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            logger.debug("claude_provider.call: exit_code=%d response_length=%d", result.returncode, len(result.stdout))
            if result.returncode != 0:
                return None
            wrapper = json.loads(result.stdout)
            if wrapper.get("is_error"):
                return None
            text = wrapper.get("result", result.stdout).strip()
            if extract_json:
                text = _extract_json(text)
            logger.debug("claude_provider.call response: %s", text[:500])
            from yoto_lib.billing.costs import get_tracker, is_subscription
            get_tracker().record(f"claude_{model}", subscription=is_subscription(f"claude_{model}"))
            return text
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            logger.debug("claude_provider.call: failed with %s", type(exc).__name__)
            return None
