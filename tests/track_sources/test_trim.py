"""Tests for yoto_lib.track_sources._trim."""

from __future__ import annotations

import pytest

from yoto_lib.track_sources._trim import PRODUCTION_PARAMS, select_boundaries


def _legacy_boundaries(ranges):
    """Oracle: exact reproduction of pre-refactor youtube.py:_trim_silence selection."""
    if len(ranges) < 2:
        return None
    start, end = ranges[0][1], ranges[-1][0]
    return (start, end) if end > start else None


@pytest.mark.parametrize(
    "ranges",
    [
        [],
        [(0.0, 0.8)],
        [(0.0, 0.8), (180.0, 181.0)],
        [(0.0, 0.8), (60.0, 60.7), (180.0, 181.0)],
        [(0.0, 5.0), (4.0, 6.0)],  # degenerate: end <= start
    ],
)
def test_select_boundaries_matches_legacy_under_production_params(ranges):
    """INVARIANT: with PRODUCTION_PARAMS, selection equals today's behavior."""
    assert select_boundaries(ranges, duration=200.0, params=PRODUCTION_PARAMS) == _legacy_boundaries(ranges)


def test_min_gap_size_filters_small_gaps():
    """A within-song gap smaller than min_gap_size is ignored (failure mode b)."""
    ranges = [(0.0, 1.0), (90.0, 90.3), (180.0, 181.0)]  # middle gap is 0.3s
    params = {**PRODUCTION_PARAMS, "min_gap_size": 0.5}
    # middle gap dropped; start=end-of-first(1.0), end=start-of-last(180.0)
    assert select_boundaries(ranges, 200.0, params) == (1.0, 180.0)


def test_require_edge_gap_rejects_interior_gaps():
    """Only gaps near the edges count as boundaries (failure mode b)."""
    ranges = [(8.0, 9.0), (100.0, 101.0)]  # neither within 3s of start(0)/end(200)
    params = {**PRODUCTION_PARAMS, "require_edge_gap": 3.0}
    assert select_boundaries(ranges, 200.0, params) is None


def test_start_gap_second_from_front():
    ranges = [(0.0, 1.0), (3.0, 4.0), (190.0, 191.0)]
    params = {**PRODUCTION_PARAMS, "start_gap": 2}
    assert select_boundaries(ranges, 200.0, params) == (4.0, 190.0)
