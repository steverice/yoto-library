"""Phase 0 diagnostic: bucket the trim over-aggression failure modes.

Imports `_parse_silence_ranges` from `yoto_lib.track_sources._trim` (the
refactored home of gap logic). `download()` is now always untrimmed (fetch-only);
no `trim=` kwarg is accepted.
Usage:
    python validation/trim_diagnose.py <url> [<url> ...]
    python validation/trim_diagnose.py --save-to tests/fixtures/trim_corpus <url> ...

Downloads each URL (untrimmed), runs silencedetect, prints the boundaries the
current algorithm WOULD choose, then prompts the human to record where the song
actually starts/ends and categorize the failure: (a) threshold, (b) wrong-gap,
(c) no-gap, or clean. With ``--save-to`` the downloaded audio is persisted so
labeling can pick up where the diagnostic left off; without it, files land in a
temp dir and are deleted.
"""

from __future__ import annotations

import argparse
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from yoto_lib.track_sources._trim import _parse_silence_ranges
from yoto_lib.track_sources.youtube import YouTubeProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_CORPUS_DIR = "tests/fixtures/trim_corpus"


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


@contextmanager
def _output_dir(save_to: Path | None) -> Iterator[Path]:
    """Yield a directory to download into.

    With ``save_to`` set, ensure it exists and yield it (audio persists). With
    ``None``, yield a fresh tempdir that is cleaned up on exit.
    """
    if save_to is not None:
        save_to.mkdir(parents=True, exist_ok=True)
        yield save_to
    else:
        with TemporaryDirectory() as tmp:
            yield Path(tmp)


def main(urls: list[str], save_to: str | Path | None = None) -> None:
    provider = YouTubeProvider()
    save_dir = Path(save_to) if save_to else None
    for url in urls:
        with _output_dir(save_dir) as out_dir:
            audio, meta = provider.download(url, out_dir)  # download() is now fetch-only / untrimmed
            ranges = silencedetect(audio)
            chosen = current_boundaries(ranges)
            print(f"\n=== {meta['title']} ===")
            print(f"  url: {url}")
            print(f"  silence gaps ({len(ranges)}): {ranges}")
            print(f"  current algorithm would trim to: {chosen}")
            if save_dir is not None:
                print(f"  saved to: {audio}")
            print("  -> Listen, find the TRUE song start/end, and bucket: a/b/c/clean")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 0 trim diagnostic.",
        allow_abbrev=False,
    )
    parser.add_argument("urls", nargs="+", help="YouTube URLs to diagnose")
    parser.add_argument(
        "--save-to",
        nargs="?",
        const=DEFAULT_CORPUS_DIR,
        default=None,
        metavar="DIR",
        dest="save_to",
        help=(
            "Save downloaded audio to DIR so it can be labeled later "
            f"(default when given without a value: {DEFAULT_CORPUS_DIR}). "
            "Without this flag, files go to a temp dir and are deleted."
        ),
    )
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    main(args.urls, save_to=args.save_to)
