"""YouTube source provider — download audio via yt-dlp."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

_YOUTUBE_PATTERNS = [
    re.compile(r"https?://(www\.)?youtube\.com/watch"),
    re.compile(r"https?://youtu\.be/"),
    re.compile(r"https?://(www\.)?youtube\.com/shorts/"),
    re.compile(r"https?://music\.youtube\.com/watch"),
]


def _sanitize_filename(title: str) -> str:
    """Minimal sanitization for macOS filenames: strip / and :, trim whitespace."""
    return title.replace("/", "").replace(":", "").strip()


def _parse_and_call_progress(
    line: str,
    on_progress: Callable[[float, int, int | None, str], None],
) -> None:
    """Parse a yt-dlp structured progress line and invoke the callback.

    Expected format (whitespace-separated):
      downloaded_bytes total_bytes speed percentage
    Any field may be "NA" or "N/A" when unknown.
    """
    parts = line.split()
    if len(parts) < 4:
        return
    try:
        downloaded_raw, total_raw, speed_raw, pct_raw = parts[0], parts[1], parts[2], parts[3]

        downloaded = int(float(downloaded_raw)) if downloaded_raw not in ("NA", "N/A", "None") else 0
        total: int | None = int(float(total_raw)) if total_raw not in ("NA", "N/A", "None") else None
        pct_raw = pct_raw.rstrip("%")
        pct = float(pct_raw) if pct_raw not in ("NA", "N/A", "None") else 0.0
        speed = speed_raw if speed_raw not in ("NA", "N/A", "None") else ""
        on_progress(pct, downloaded, total, speed)
    except (ValueError, IndexError):
        pass  # Malformed line — ignore silently


class YouTubeProvider:
    def can_handle(self, url: str) -> bool:
        """Return True if url is a YouTube video URL."""
        return any(p.match(url) for p in _YOUTUBE_PATTERNS)

    def download(
        self,
        url: str,
        output_dir: Path,
        on_progress: Callable[[float, int, int | None, str], None] | None = None,
    ) -> tuple[Path, dict[str, str]]:
        """Download audio from a YouTube URL via yt-dlp.

        Returns (audio_path, metadata_dict) for the raw (untrimmed) audio.
        Raises RuntimeError if yt-dlp is not installed or download fails.

        Args:
            on_progress: Optional callback invoked for each progress update.
                Called with (pct, downloaded_bytes, total_bytes_or_None, speed_str).
        """
        logger.debug("youtube: downloading %s", url)
        # Fetch video metadata
        try:
            meta_result = subprocess.run(
                ["yt-dlp", "--dump-json", "--no-download", url],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("yt-dlp is required for YouTube downloads. Install with: brew install yt-dlp") from exc

        if meta_result.returncode != 0:
            raise RuntimeError(f"yt-dlp metadata fetch failed: {meta_result.stderr}")

        info = json.loads(meta_result.stdout)
        title = info.get("title", "untitled")
        safe_title = _sanitize_filename(title)
        logger.debug("youtube: title='%s'", title)

        # Download best audio using Popen for real-time progress
        output_template = str(output_dir / f"{safe_title}.%(ext)s")
        dl_cmd = [
            "yt-dlp",
            "-x",
            "--audio-quality",
            "0",
            "--newline",
            "--progress-template",
            "download:%(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.speed)s %(progress.percentage)s",  # noqa: E501
            "-o",
            output_template,
            url,
        ]
        logger.debug("youtube: %s", " ".join(dl_cmd))

        stderr_lines: list[str] = []
        try:
            proc = subprocess.Popen(
                dl_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("yt-dlp is required for YouTube downloads. Install with: brew install yt-dlp") from exc

        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip("\n")
            stderr_lines.append(line)
            if line.startswith("download:") and on_progress:
                _parse_and_call_progress(line[len("download:") :], on_progress)

        proc.wait()
        exit_code = proc.returncode
        logger.debug("youtube: yt-dlp exit_code=%d", exit_code)
        if exit_code != 0:
            raise RuntimeError(f"yt-dlp download failed: {chr(10).join(stderr_lines)}")

        # Find the downloaded file (yt-dlp chooses the extension)
        downloaded = [
            p
            for p in output_dir.iterdir()
            if p.stem == safe_title and p.suffix.lower() not in (".webloc", ".mka", ".jsonl")
        ]
        if not downloaded:
            raise RuntimeError(f"yt-dlp produced no output file for: {url}")
        audio_path = downloaded[0]
        logger.debug("youtube: downloaded to %s", audio_path.name)
        metadata = {"title": title, "source_url": url}
        return audio_path, metadata
