"""Memoizing Analyzer for the sweep: one ffmpeg pass per (path, signature, ffmpeg-version)."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from yoto_lib.track_sources._trim import DirectAnalyzer

if TYPE_CHECKING:
    from pathlib import Path

    from yoto_lib.track_sources._trim import Analyzer


def detect_ffmpeg_version() -> str:
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()[0]


class CachingAnalyzer:
    """Wraps an Analyzer, caching results. ffmpeg version is part of the key."""

    def __init__(self, inner: Analyzer | None = None, ffmpeg_version: str | None = None) -> None:
        self._inner = inner or DirectAnalyzer()
        self._version = ffmpeg_version or detect_ffmpeg_version()
        self._silence_cache: dict[tuple, list[tuple[float, float]]] = {}
        self._duration_cache: dict[Path, float] = {}

    def silence_ranges(self, path: Path, *, noise_db: float, min_duration: float) -> list[tuple[float, float]]:
        key = (str(path), noise_db, min_duration, self._version)
        if key not in self._silence_cache:
            self._silence_cache[key] = self._inner.silence_ranges(path, noise_db=noise_db, min_duration=min_duration)
        return self._silence_cache[key]

    def duration(self, path: Path) -> float:
        if path not in self._duration_cache:
            self._duration_cache[path] = self._inner.duration(path)
        return self._duration_cache[path]
