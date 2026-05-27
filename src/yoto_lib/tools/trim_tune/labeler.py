"""Produce/update corpus labels. `audition` plays candidate boundaries via ffplay."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from yoto_lib.tools.trim_tune.corpus import content_hash

if TYPE_CHECKING:
    from pathlib import Path


def _probe_duration(path: Path) -> float:
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


def _ytdlp_version() -> str:
    try:
        out = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def audition(audio: Path, at: float, window: float = 4.0) -> None:
    """Play `window` seconds starting at `at` so the labeler can hear a boundary."""
    subprocess.run(
        [
            "ffplay",
            "-autoexit",
            "-nodisp",
            "-ss",
            str(at),
            "-t",
            str(window),
            str(audio),
        ],
        check=False,
    )


def upsert_label(
    corpus_dir: Path,
    *,
    filename: str,
    source_url: str,
    category: str,
    true_start_max: float,
    true_end_min: float,
    true_start_min: float | None = None,
    true_end_max: float | None = None,
) -> None:
    """Insert or replace a label entry, auto-filling hash/duration/yt-dlp version."""
    labels_path = corpus_dir / "labels.json"
    entries = json.loads(labels_path.read_text()) if labels_path.exists() else []
    entries = [e for e in entries if e["filename"] != filename]
    audio = corpus_dir / filename
    entries.append(
        {
            "filename": filename,
            "source_url": source_url,
            "ytdlp_version": _ytdlp_version(),
            "content_hash": content_hash(audio),
            "duration": _probe_duration(audio),
            "category": category,
            "true_start_max": true_start_max,
            "true_end_min": true_end_min,
            "true_start_min": true_start_min,
            "true_end_max": true_end_max,
        }
    )
    labels_path.write_text(json.dumps(entries, indent=2))
