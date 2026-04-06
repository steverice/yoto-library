"""Cost tracking for AI provider calls."""

from __future__ import annotations

import json
import os
import threading
from collections import Counter
from pathlib import Path

_COSTS_FILE = Path(__file__).parent / "costs.json"


def _load_costs() -> dict[str, dict]:
    with open(_COSTS_FILE, encoding="utf-8") as f:
        return json.load(f)


COSTS = _load_costs()


def is_subscription(provider: str) -> bool:
    """Check if a provider is using subscription billing based on env."""
    if provider.startswith("claude_"):
        return "ANTHROPIC_API_KEY" not in os.environ
    return False


class CostTracker:
    """Thread-safe accumulator for API call costs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[tuple[str, float, bool]] = []

    def record(self, key: str, *, count: int = 1, subscription: bool = False) -> None:
        entry = COSTS.get(key)
        if entry is None:
            return
        unit_cost = 0.0 if subscription else entry["cost"]
        with self._lock:
            for _ in range(count):
                self._records.append((key, unit_cost, subscription))

    @property
    def total(self) -> float:
        with self._lock:
            return sum(cost for _, cost, _ in self._records)

    def has_records(self) -> bool:
        with self._lock:
            return len(self._records) > 0

    def summary_lines(self) -> list[str]:
        with self._lock:
            records = list(self._records)
        if not records:
            return []

        counts: Counter[str] = Counter()
        totals: dict[str, float] = {}
        sub_flags: dict[str, bool] = {}
        for key, cost, is_sub in records:
            counts[key] += 1
            totals[key] = totals.get(key, 0.0) + cost
            sub_flags[key] = is_sub

        billable = []
        subscription = []
        for key in counts:
            label = COSTS.get(key, {}).get("label", key)
            n = counts[key]
            if sub_flags[key]:
                subscription.append(f"{n}x {label}")
            else:
                billable.append((label, n, totals[key]))

        lines = []
        if billable:
            parts = [f"{n}x {label}" for label, n, _ in billable]
            total = sum(t for _, _, t in billable)
            lines.append(f"Estimated cost: ${total:.3f} ({', '.join(parts)})")
        if subscription:
            lines.append(f"  + included with subscription ({', '.join(subscription)})")

        return lines


_tracker: CostTracker | None = None


def get_tracker() -> CostTracker:
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker


def reset_tracker() -> CostTracker:
    global _tracker
    _tracker = CostTracker()
    return _tracker
