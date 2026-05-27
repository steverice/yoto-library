"""Phase 0 diagnostic: bucket the trim over-aggression failure modes.

Imports `_parse_silence_ranges` from `yoto_lib.track_sources._trim` (the
refactored home of gap logic). `download()` is now always untrimmed (fetch-only);
no `trim=` kwarg is accepted.
Usage:
    python validation/trim_diagnose.py <url> [<url> ...]
Downloads each URL (untrimmed), runs silencedetect, prints the boundaries the
current algorithm WOULD choose, then prompts the human to record where the song
actually starts/ends and categorize the failure: (a) threshold, (b) wrong-gap,
(c) no-gap, or clean.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from yoto_lib.track_sources._trim import _parse_silence_ranges
from yoto_lib.track_sources.youtube import YouTubeProvider


def current_boundaries(ranges: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Reproduce today's youtube.py:_trim_silence gap selection."""
    if len(ranges) < 2:
        return None
    start, end = ranges[0][1], ranges[-1][0]
    return (start, end) if end > start else None


def silencedetect(audio: Path) -> list[tuple[float, float]]:
    result = subprocess.run(
        ["ffmpeg", "-i", str(audio), "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    return _parse_silence_ranges(result.stderr)


def main(urls: list[str]) -> None:
    provider = YouTubeProvider()
    for url in urls:
        with TemporaryDirectory() as tmp:
            audio, meta = provider.download(url, Path(tmp))  # download() is now fetch-only / untrimmed
            ranges = silencedetect(audio)
            chosen = current_boundaries(ranges)
            print(f"\n=== {meta['title']} ===")
            print(f"  url: {url}")
            print(f"  silence gaps ({len(ranges)}): {ranges}")
            print(f"  current algorithm would trim to: {chosen}")
            print("  -> Listen, find the TRUE song start/end, and bucket: a/b/c/clean")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python validation/trim_diagnose.py <url> [<url> ...]")
        raise SystemExit(2)
    main(sys.argv[1:])
