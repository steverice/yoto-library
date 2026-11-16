"""Tests for yoto_lib.track_sources._trim."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from yoto_lib.track_sources._trim import (
    PRODUCTION_PARAMS,
    DirectAnalyzer,
    SilenceStrategy,
    _parse_silence_ranges,
    apply_cut,
    select_boundaries,
    trim_audio,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed"
)


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


def _probe_duration(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


@needs_ffmpeg
def test_direct_analyzer_reports_duration(longer_wav):
    """DirectAnalyzer.duration matches ffprobe within tolerance."""
    assert abs(DirectAnalyzer().duration(longer_wav) - 1.0) < 0.05


@needs_ffmpeg
def test_apply_cut_trims_in_place_and_returns_same_path(longer_wav):
    """apply_cut cuts [0.25, 0.75] and replaces the file in place."""
    result = apply_cut(longer_wav, 0.25, 0.75)
    assert result == longer_wav
    assert result.exists()
    assert abs(_probe_duration(result) - 0.5) < 0.1


def test_apply_cut_returns_original_when_ffmpeg_fails(longer_wav, monkeypatch):
    """Non-zero ffmpeg exit leaves the original untouched."""
    original_bytes = longer_wav.read_bytes()

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "boom")

    monkeypatch.setattr("yoto_lib.track_sources._trim.subprocess.run", fake_run)
    result = apply_cut(longer_wav, 0.25, 0.75)
    assert result == longer_wav
    assert result.read_bytes() == original_bytes  # unchanged


@needs_ffmpeg
def test_trim_audio_no_trim_returns_unchanged(longer_wav):
    """A tone with <2 silence gaps is returned unchanged (None prediction)."""
    result = trim_audio(longer_wav)
    assert result == longer_wav
    assert abs(_probe_duration(result) - 1.0) < 0.1


@needs_ffmpeg
def test_silence_strategy_uses_analyzer(longer_wav):
    """SilenceStrategy.trim delegates to select_boundaries over analyzer output."""
    pred = SilenceStrategy().trim(longer_wav, PRODUCTION_PARAMS, DirectAnalyzer())
    assert pred is None or (isinstance(pred, tuple) and len(pred) == 2)


def test_parse_silence_ranges_from_ffmpeg_output():
    """_parse_silence_ranges extracts (start, end) pairs from ffmpeg stderr."""
    stderr = (
        "[silencedetect @ 0x1234] silence_start: 0\n"
        "[silencedetect @ 0x1234] silence_end: 1.5 | silence_duration: 1.5\n"
        "[silencedetect @ 0x1234] silence_start: 30.2\n"
        "[silencedetect @ 0x1234] silence_end: 31.0 | silence_duration: 0.8\n"
        "[silencedetect @ 0x1234] silence_start: 65.0\n"
        "[silencedetect @ 0x1234] silence_end: 66.5 | silence_duration: 1.5\n"
    )
    assert _parse_silence_ranges(stderr) == [(0.0, 1.5), (30.2, 31.0), (65.0, 66.5)]


def test_parse_silence_ranges_empty():
    """_parse_silence_ranges returns empty list when no silence found."""
    assert _parse_silence_ranges("some random output\n") == []


def test_parse_silence_ranges_unterminated():
    """A silence_start without a matching silence_end is ignored."""
    stderr = (
        "[silencedetect @ 0x1234] silence_start: 0\n"
        "[silencedetect @ 0x1234] silence_end: 1.5 | silence_duration: 1.5\n"
        "[silencedetect @ 0x1234] silence_start: 60.0\n"
    )
    assert _parse_silence_ranges(stderr) == [(0.0, 1.5)]


def test_param_grid_dedups_and_includes_production_defaults():
    grid = list(SilenceStrategy().param_grid())
    assert PRODUCTION_PARAMS in grid
    keys = {tuple(sorted(p.items(), key=lambda kv: (kv[0], kv[1] is None, kv[1]))) for p in grid}
    assert len(grid) == len(keys)  # no duplicates
