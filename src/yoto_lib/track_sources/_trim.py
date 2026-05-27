"""Shared audio trimmer — predict boundaries and apply the cut.

Used by both the production download pipeline and the tuning harness, so the
parameters that ship are the parameters that get tuned.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)

_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")

# Single source of truth for shipped trim behavior. Defaults reproduce the
# pre-refactor youtube.py:_trim_silence exactly (verified by the invariant test).
PRODUCTION_PARAMS: dict = {
    "noise_db": -30.0,
    "min_duration": 0.5,
    "start_gap": 1,  # 1-based index from the front
    "end_gap": 1,  # 1-based index from the back
    "min_gap_size": None,  # None = no filtering beyond silencedetect's own d=
    "require_edge_gap": None,  # None = a gap anywhere can be a boundary
}


def _parse_silence_ranges(stderr: str) -> list[tuple[float, float]]:
    """Parse ffmpeg silencedetect output into (start, end) pairs."""
    ranges: list[tuple[float, float]] = []
    current_start: float | None = None
    for line in stderr.splitlines():
        m = _SILENCE_START_RE.search(line)
        if m:
            current_start = float(m.group(1))
            continue
        m = _SILENCE_END_RE.search(line)
        if m and current_start is not None:
            ranges.append((current_start, float(m.group(1))))
            current_start = None
    return ranges


def select_boundaries(
    ranges: list[tuple[float, float]],
    duration: float,
    params: dict,
) -> tuple[float, float] | None:
    """Choose (start, end) trim boundaries from silence ranges, or None to skip.

    Reproduces legacy behavior under PRODUCTION_PARAMS; the extra knobs target
    the wrong-gap failure mode.
    """
    min_gap = params.get("min_gap_size")
    gaps = ranges if min_gap is None else [(s, e) for (s, e) in ranges if (e - s) >= min_gap]

    edge = params.get("require_edge_gap")
    if edge is None:
        start_candidates = gaps
        end_candidates = gaps
    else:
        start_candidates = [(s, e) for (s, e) in gaps if s <= edge]
        end_candidates = [(s, e) for (s, e) in gaps if e >= duration - edge]

    start_gap = params["start_gap"]
    end_gap = params["end_gap"]
    if len(start_candidates) < start_gap or len(end_candidates) < end_gap:
        return None

    start = start_candidates[start_gap - 1][1]  # end of the chosen front gap
    end = end_candidates[-end_gap][0]  # start of the chosen back gap
    if end <= start:
        return None
    return (start, end)


class Analyzer(Protocol):
    def silence_ranges(self, path: Path, *, noise_db: float, min_duration: float) -> list[tuple[float, float]]: ...
    def duration(self, path: Path) -> float: ...


class DirectAnalyzer:
    """Runs ffmpeg/ffprobe directly. Stateless; safe to share across threads."""

    def silence_ranges(self, path: Path, *, noise_db: float, min_duration: float) -> list[tuple[float, float]]:
        result = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-"],
            capture_output=True,
            text=True,
        )
        return _parse_silence_ranges(result.stderr)

    def duration(self, path: Path) -> float:
        result = subprocess.run(
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
        return float(result.stdout.strip())


class TrimStrategy(Protocol):
    name: str

    def param_grid(self) -> Iterable[dict]: ...
    def trim(self, audio_path: Path, params: dict, analyze: Analyzer) -> tuple[float, float] | None: ...


class SilenceStrategy:
    name = "silence"

    def param_grid(self) -> Iterable[dict]:  # implemented in a later task
        raise NotImplementedError

    def trim(self, audio_path: Path, params: dict, analyze: Analyzer) -> tuple[float, float] | None:
        ranges = analyze.silence_ranges(audio_path, noise_db=params["noise_db"], min_duration=params["min_duration"])
        return select_boundaries(ranges, analyze.duration(audio_path), params)


def apply_cut(audio_path: Path, start: float, end: float) -> Path:
    """Cut [start, end] with stream copy, replacing the file in place.

    Returns the same path. On ffmpeg failure, returns the original unchanged.
    """
    trimmed = audio_path.with_stem(audio_path.stem + "_trimmed")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path), "-ss", str(start), "-to", str(end), "-c", "copy", str(trimmed)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.debug("apply_cut: ffmpeg failed (exit %d)", result.returncode)
        trimmed.unlink(missing_ok=True)
        return audio_path
    audio_path.unlink()
    trimmed.rename(audio_path)
    return audio_path


def trim_audio(
    audio_path: Path,
    *,
    strategy: TrimStrategy | None = None,
    params: dict | None = None,
    analyzer: Analyzer | None = None,
) -> Path:
    """Production trim entry point: predict boundaries, then apply the cut."""
    strategy = strategy or SilenceStrategy()
    params = PRODUCTION_PARAMS if params is None else params
    analyzer = analyzer or DirectAnalyzer()
    boundaries = strategy.trim(audio_path, params, analyzer)
    if boundaries is None:
        return audio_path
    return apply_cut(audio_path, *boundaries)
