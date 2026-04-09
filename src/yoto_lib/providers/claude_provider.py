"""Claude provider — calls Claude via Anthropic SDK or CLI subprocess."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess

from yoto_lib.providers.base import Provider, ProviderStatus, StatusPageMixin

logger = logging.getLogger(__name__)

# Map shortnames used by callers to full API model IDs
_MODEL_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6-20250514",
}


def _extract_json(text: str) -> str:
    """Strip markdown code fences from Claude output to get raw JSON."""
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


class ClaudeProvider(StatusPageMixin, Provider):
    """Calls Claude for text/vision tasks (matching, comparison, description).

    Uses the Anthropic SDK when ANTHROPIC_API_KEY is set, otherwise falls
    back to the Claude CLI subprocess.
    """

    display_name = "Claude"
    status_page_url = "https://status.claude.com/api/v2/status.json"

    @classmethod
    def check_status(cls) -> ProviderStatus:
        if os.environ.get("ANTHROPIC_API_KEY"):
            return super().check_status()
        if not shutil.which("claude"):
            return ProviderStatus(healthy=False, message="Claude CLI not found on PATH")
        return super().check_status()

    @property
    def is_subscription(self) -> bool:
        """Subscription when using CLI (no API key), pay-per-call with SDK."""
        return "ANTHROPIC_API_KEY" not in os.environ

    def call(
        self,
        prompt: str,
        *,
        allowed_tools: str = "",
        timeout: int = 120,
        model: str = "haiku",
        extract_json: bool = True,
    ) -> str | None:
        """Call Claude and return the response text, or None on failure.

        Args:
            prompt: The prompt to send.
            allowed_tools: Comma-separated tool names (e.g. "Read"). CLI-only, ignored with SDK.
            timeout: Timeout in seconds.
            model: Claude model shortname (haiku, sonnet).
            extract_json: If True, strip markdown code fences from the response.
        """
        if os.environ.get("ANTHROPIC_API_KEY"):
            return self._call_sdk(prompt, model=model, timeout=timeout, extract_json=extract_json)
        return self._call_cli(prompt, model=model, timeout=timeout, extract_json=extract_json, allowed_tools=allowed_tools)

    def _call_sdk(
        self,
        prompt: str,
        *,
        model: str,
        timeout: int,
        extract_json: bool,
    ) -> str | None:
        """Call Claude via the Anthropic SDK."""
        try:
            import anthropic
        except ImportError:
            logger.warning("anthropic package not installed, falling back to CLI")
            return self._call_cli(prompt, model=model, timeout=timeout, extract_json=extract_json, allowed_tools="")

        model_id = _MODEL_MAP.get(model, model)

        try:
            logger.debug("claude_provider.call_sdk: model=%s prompt_length=%d", model_id, len(prompt))
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=model_id,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
            text = response.content[0].text.strip()
            logger.debug("claude_provider.call_sdk: response_length=%d", len(text))

            if extract_json:
                text = _extract_json(text)

            logger.debug("claude_provider.call_sdk response: %s", text[:500])
            from yoto_lib.billing.costs import get_tracker
            get_tracker().record(f"claude_{model}", subscription=self.is_subscription)
            return text
        except Exception as exc:
            logger.debug("claude_provider.call_sdk: failed with %s: %s", type(exc).__name__, exc)
            return None

    def _call_cli(
        self,
        prompt: str,
        *,
        model: str,
        timeout: int,
        extract_json: bool,
        allowed_tools: str,
    ) -> str | None:
        """Call Claude via the CLI subprocess."""
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
            logger.debug("claude_provider.call_cli: model=%s prompt_length=%d", model, len(prompt))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            logger.debug("claude_provider.call_cli: exit_code=%d response_length=%d", result.returncode, len(result.stdout))
            if result.returncode != 0:
                return None
            wrapper = json.loads(result.stdout)
            if wrapper.get("is_error"):
                return None
            text = wrapper.get("result", result.stdout).strip()
            if extract_json:
                text = _extract_json(text)
            logger.debug("claude_provider.call_cli response: %s", text[:500])
            from yoto_lib.billing.costs import get_tracker
            get_tracker().record(f"claude_{model}", subscription=self.is_subscription)
            return text
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            logger.debug("claude_provider.call_cli: failed with %s", type(exc).__name__)
            return None
