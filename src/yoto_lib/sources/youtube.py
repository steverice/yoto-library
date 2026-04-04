"""YouTube source provider — download audio via yt-dlp."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_YOUTUBE_PATTERNS = [
    re.compile(r"https?://(www\.)?youtube\.com/watch"),
    re.compile(r"https?://youtu\.be/"),
    re.compile(r"https?://(www\.)?youtube\.com/shorts/"),
    re.compile(r"https?://music\.youtube\.com/watch"),
]


def _sanitize_filename(title: str) -> str:
    """Minimal sanitization for macOS filenames: strip / and :, trim whitespace."""
    return title.replace("/", "").replace(":", "").strip()


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
