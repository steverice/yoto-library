"""YouTube source provider — download audio via yt-dlp."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")

_YOUTUBE_PATTERNS = [
    re.compile(r"https?://(www\.)?youtube\.com/watch"),
    re.compile(r"https?://youtu\.be/"),
    re.compile(r"https?://(www\.)?youtube\.com/shorts/"),
    re.compile(r"https?://music\.youtube\.com/watch"),
]


def _sanitize_filename(title: str) -> str:
    """Minimal sanitization for macOS filenames: strip / and :, trim whitespace."""
    return title.replace("/", "").replace(":", "").strip()


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


def _trim_silence(audio_path: Path) -> Path:
    """Trim pre-roll and post-roll from audio using silence detection.

    Runs ffmpeg silencedetect, then extracts the segment between the end
    of the first silence gap and the start of the last silence gap.
    Returns the original path unchanged if fewer than 2 gaps are found.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(audio_path),
            "-af", "silencedetect=noise=-30dB:d=0.5",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    ranges = _parse_silence_ranges(result.stderr)

    if len(ranges) < 2:
        return audio_path

    start = ranges[0][1]   # end of first silence gap
    end = ranges[-1][0]    # start of last silence gap

    if end <= start:
        return audio_path

    trimmed_path = audio_path.with_stem(audio_path.stem + "_trimmed")
    trim_result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ss", str(start), "-to", str(end),
            "-c", "copy", str(trimmed_path),
        ],
        capture_output=True, text=True,
    )
    if trim_result.returncode != 0:
        return audio_path

    audio_path.unlink()
    trimmed_path.rename(audio_path)
    return audio_path


class YouTubeProvider:
    def can_handle(self, url: str) -> bool:
        """Return True if url is a YouTube video URL."""
        return any(p.match(url) for p in _YOUTUBE_PATTERNS)

    def download(self, url: str, output_dir: Path, trim: bool = True) -> tuple[Path, dict[str, str]]:
        """Download audio from a YouTube URL via yt-dlp.

        Returns (audio_path, metadata_dict).
        Raises RuntimeError if yt-dlp is not installed or download fails.
        """
        # Fetch video metadata
        try:
            meta_result = subprocess.run(
                ["yt-dlp", "--dump-json", "--no-download", url],
                capture_output=True, text=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "yt-dlp is required for YouTube downloads. "
                "Install with: brew install yt-dlp"
            )

        if meta_result.returncode != 0:
            raise RuntimeError(f"yt-dlp metadata fetch failed: {meta_result.stderr}")

        info = json.loads(meta_result.stdout)
        title = info.get("title", "untitled")
        safe_title = _sanitize_filename(title)

        # Download best audio
        output_template = str(output_dir / f"{safe_title}.%(ext)s")
        result = subprocess.run(
            ["yt-dlp", "-x", "--audio-quality", "0", "-o", output_template, url],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp download failed: {result.stderr}")

        # Find the downloaded file (yt-dlp chooses the extension)
        downloaded = [
            p for p in output_dir.iterdir()
            if p.stem == safe_title and p.suffix.lower() not in (".webloc", ".mka", ".jsonl")
        ]
        if not downloaded:
            raise RuntimeError(f"yt-dlp produced no output file for: {url}")
        audio_path = downloaded[0]

        # Trim silence if requested
        if trim:
            audio_path = _trim_silence(audio_path)

        metadata = {"title": title, "source_url": url}
        return audio_path, metadata
