"""Tests for the cost tracking system."""

import json
import re
import threading
from pathlib import Path

import pytest

from yoto_lib.costs import COSTS, CostTracker, _COSTS_FILE


def test_costs_json_valid():
    """Every entry in costs.json has a float cost and string label."""
    with open(_COSTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) > 0
    for key, entry in data.items():
        assert isinstance(entry["cost"], (int, float)), f"{key}: cost must be a number"
        assert entry["cost"] >= 0, f"{key}: cost must be non-negative"
        assert isinstance(entry["label"], str), f"{key}: label must be a string"
        assert len(entry["label"]) > 0, f"{key}: label must not be empty"


def test_tracker_basic():
    t = CostTracker()
    t.record("retrodiffusion")
    t.record("retrodiffusion", count=2)
    assert t.total == pytest.approx(0.009)
    assert t.has_records()


def test_tracker_empty():
    t = CostTracker()
    assert t.total == 0.0
    assert not t.has_records()
    assert t.summary_lines() == []


def test_tracker_subscription_free():
    t = CostTracker()
    t.record("claude_haiku", subscription=True)
    t.record("claude_sonnet", subscription=True)
    assert t.total == 0.0
    assert t.has_records()


def test_tracker_mixed():
    t = CostTracker()
    t.record("retrodiffusion", count=3)
    t.record("claude_haiku", subscription=True)
    assert t.total == pytest.approx(0.009)


def test_tracker_unknown_key():
    t = CostTracker()
    t.record("nonexistent_provider")
    assert t.total == 0.0
    assert not t.has_records()


def test_tracker_thread_safety():
    t = CostTracker()
    n_threads = 10
    n_per_thread = 100

    def worker():
        for _ in range(n_per_thread):
            t.record("retrodiffusion")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    expected = n_threads * n_per_thread * COSTS["retrodiffusion"]["cost"]
    assert t.total == pytest.approx(expected)


def test_tracker_summary_billable():
    t = CostTracker()
    t.record("retrodiffusion", count=3)
    lines = t.summary_lines()
    assert len(lines) == 1
    assert "3x RetroDiffusion icon" in lines[0]
    assert "$0.009" in lines[0]


def test_tracker_summary_with_subscription():
    t = CostTracker()
    t.record("retrodiffusion", count=3)
    t.record("claude_sonnet", subscription=True)
    lines = t.summary_lines()
    assert len(lines) == 2
    assert "$0.009" in lines[0]
    assert "subscription" in lines[1]
    assert "Claude Sonnet" in lines[1]


# ── README sync ──────────────────────────────────────────────────────────────

# Map README task prefixes to costs.json keys
_README_TO_COSTS = {
    "Icon generation": "retrodiffusion",
    "Album art recomposition": "flux_recompose",
    "Text layer rendering": "gemini_flash_image",
    "Text-to-image cover generation": "openai_generate",
}


def test_readme_matches_costs_json():
    """Verify the README pricing table is consistent with costs.json."""
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")

    # Find pricing table rows
    table_rows = re.findall(r"^\|(.+)\|$", readme, re.MULTILINE)
    # Skip header and separator rows
    data_rows = [r for r in table_rows if not r.startswith("---") and "Task" not in r]

    matched = 0
    for row in data_rows:
        cells = [c.strip() for c in row.split("|")]
        if len(cells) < 4:
            continue
        task, _, _, pricing = cells[0], cells[1], cells[2], cells[3]

        # Find which costs.json key this row maps to
        costs_key = None
        for prefix, key in _README_TO_COSTS.items():
            if task.startswith(prefix):
                costs_key = key
                break

        if costs_key is None:
            # Claude row — just verify it says "subscription" or "Included"
            if "Claude" in task or "Descriptions" in task:
                assert "subscription" in pricing.lower() or "included" in pricing.lower(), \
                    f"Claude row should mention subscription: {pricing}"
            continue

        # Extract dollar amount from README (e.g. "~$0.003/image" → 0.003)
        match = re.search(r"\$([0-9.]+)", pricing)
        assert match, f"Could not parse price from README row: {pricing}"
        readme_cost = float(match.group(1))

        json_cost = COSTS[costs_key]["cost"]
        assert readme_cost == pytest.approx(json_cost), \
            f"README says ${readme_cost} for {task}, but costs.json says ${json_cost} for {costs_key}"
        matched += 1

    assert matched == len(_README_TO_COSTS), \
        f"Expected to match {len(_README_TO_COSTS)} rows, but only matched {matched}"
