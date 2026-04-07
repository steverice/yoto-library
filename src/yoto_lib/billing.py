"""Billing persistence and provider balance/usage queries."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from yoto_lib.costs import CostTracker

logger = logging.getLogger(__name__)

BILLING_FILE = Path.home() / ".yoto" / "billing.json"

# Provider group names -> cost keys
PROVIDER_GROUPS: dict[str, list[str]] = {
    "retrodiffusion": ["retrodiffusion"],
    "openai": ["openai_generate", "openai_edit"],
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
