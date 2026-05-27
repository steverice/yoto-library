"""Tests for trim_tune.report."""

from __future__ import annotations

from yoto_lib.tools.trim_tune.report import survivors_vs_tolerance
from yoto_lib.tools.trim_tune.scorer import ConfigScore, FixtureScore


def test_survivors_vs_tolerance_counts_per_tolerance():
    clean = ConfigScore(params={"noise_db": -30.0}, fixtures=[FixtureScore("a", 0, 0, 0, 0)])
    clips = ConfigScore(params={"noise_db": -24.0}, fixtures=[FixtureScore("a", 0.5, 0, 0, 0)])
    rows = survivors_vs_tolerance([clean, clips], tolerances=[0.1, 0.6])
    assert rows == [(0.1, 1), (0.6, 2)]  # at 0.1 only clean survives; at 0.6 both
