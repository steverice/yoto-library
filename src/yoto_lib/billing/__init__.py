"""Billing persistence and provider balance/usage queries."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from .costs import CostTracker

logger = logging.getLogger(__name__)

BILLING_FILE = Path.home() / ".yoto" / "billing.json"

# Provider group names -> cost keys
PROVIDER_GROUPS: dict[str, list[str]] = {
    "retrodiffusion": ["retrodiffusion"],
    "openai": ["openai_generate_low", "openai_generate_medium", "openai_edit_low", "openai_edit_medium"],
    "flux": ["flux_generate", "flux_recompose"],
    "gemini": ["gemini_flash_image"],
    "claude": ["claude_haiku", "claude_sonnet"],
}

# Dashboard URLs for providers without balance APIs
DASHBOARD_URLS: dict[str, str] = {
    "OpenAI": "platform.openai.com",
    "Together AI": "api.together.ai",
    "Google Gemini": "aistudio.google.com",
    "Claude": "console.anthropic.com",
}


def persist_session(tracker: CostTracker) -> None:
    """Add current session's costs to lifetime totals in billing.json."""
    records = tracker.records()
    if not records:
        return

    totals = read_totals()

    for key, data in records.items():
        if key in totals:
            totals[key]["cost"] += data["cost"]
            totals[key]["calls"] += data["calls"]
        else:
            totals[key] = {"cost": data["cost"], "calls": data["calls"]}

    _write_totals(totals)


def read_totals() -> dict[str, dict]:
    """Read lifetime totals from billing.json. Returns {} if file missing."""
    if not BILLING_FILE.exists():
        return {}
    try:
        data = json.loads(BILLING_FILE.read_text(encoding="utf-8"))
        return data.get("totals", {})
    except (json.JSONDecodeError, OSError):
        logger.warning("billing: could not read %s", BILLING_FILE)
        return {}


def reset_totals(provider_group: str | None = None) -> None:
    """Reset lifetime totals. None = all, otherwise reset a provider group."""
    if provider_group is None:
        _write_totals({})
        return

    keys_to_remove = PROVIDER_GROUPS.get(provider_group, [])
    totals = read_totals()
    for key in keys_to_remove:
        totals.pop(key, None)
    _write_totals(totals)


def _write_totals(totals: dict) -> None:
    """Write totals dict to billing.json."""
    BILLING_FILE.parent.mkdir(parents=True, exist_ok=True)
    BILLING_FILE.write_text(
        json.dumps({"totals": totals}, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_balances() -> dict[str, dict]:
    """Fetch live balances from providers that support it. Runs in parallel.

    Returns dict like:
        {"RetroDiffusion": {"balance": 12.45}}
        {"RetroDiffusion": {"error": "timeout"}}
    Only includes providers whose API keys are configured.
    """
    tasks: dict[str, object] = {}
    if os.environ.get("RETRODIFFUSION_API_KEY"):
        tasks["RetroDiffusion"] = _fetch_retrodiffusion_balance
    if os.environ.get("TOGETHER_API_KEY") and os.environ.get("TOGETHER_ORG_ID"):
        tasks["Together AI"] = _fetch_together_balance

    results = {}
    if not tasks:
        return results

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = {"balance": future.result()}
            except Exception as exc:
                results[name] = {"error": str(exc)}

    return results


def _fetch_retrodiffusion_balance() -> float:
    """Query RetroDiffusion balance without generating images."""
    response = httpx.post(
        "https://api.retrodiffusion.ai/v1/inferences",
        headers={"X-RD-Token": os.environ["RETRODIFFUSION_API_KEY"]},
        json={
            "prompt": "test",
            "width": 64,
            "height": 64,
            "num_images": 1,
            "check_cost": True,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["remaining_balance"]


def _fetch_together_balance() -> float:
    """Query Together AI ongoing balance."""
    org_id = os.environ["TOGETHER_ORG_ID"]
    response = httpx.get(
        f"https://api.together.ai/api/billing/organizations/{org_id}/ongoing-balance",
        headers={"Authorization": f"Bearer {os.environ['TOGETHER_API_KEY']}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["totalOngoingBalanceCents"] / 100


def fetch_subscription_usage() -> dict | None:
    """Fetch Claude subscription usage via OAuth. Returns None on failure.

    Returns dict like:
        {"session": {"utilization": 32.0, "resets_at": "..."},
         "weekly": {"utilization": 30.0, "resets_at": "..."},
         "weekly_sonnet": {"utilization": 3.0, "resets_at": "..."}}
    """
    token = _get_claude_oauth_token()
    if token is None:
        return None

    try:
        response = httpx.get(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("billing: Claude OAuth usage request failed: %s", exc)
        return None

    result = {}
    if data.get("five_hour"):
        result["session"] = data["five_hour"]
    if data.get("seven_day"):
        result["weekly"] = data["seven_day"]
    if data.get("seven_day_sonnet"):
        result["weekly_sonnet"] = data["seven_day_sonnet"]

    return result or None


def _get_claude_oauth_token() -> str | None:
    """Extract Claude OAuth token from macOS keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        creds = json.loads(result.stdout.strip())
        return creds.get("claudeAiOauth", {}).get("accessToken")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
