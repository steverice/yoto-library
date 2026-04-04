"""Auto-generate playlist descriptions from track metadata."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from yoto_lib import mka

if TYPE_CHECKING:
    from yoto_lib.playlist import Playlist


def generate_description(
    playlist: "Playlist",
    log: callable = None,
) -> None:
    """Generate description.txt from track metadata via Claude CLI.

    Skips if description.txt already exists. On failure, logs a warning
    and continues (cover generation handles missing descriptions).
    """
    if playlist.description_path.exists():
        return

    _log = log or (lambda msg: None)

    # Collect metadata from tracks
    metadata = _collect_metadata(playlist)

    # Build prompt
    prompt = _build_prompt(playlist.title, metadata)

    # Call Claude CLI
    description = _call_claude(prompt)
    if description is None:
        _log("Warning: could not generate description (claude CLI unavailable or failed)")
        return

    # Truncate to 500 chars as safety net
    description = description[:500]

    # Write to disk
    playlist.description_path.write_text(description, encoding="utf-8")
    playlist.description = description
    _log(f"Generated description: {description}")


def _collect_metadata(playlist: "Playlist") -> dict[str, list[str]]:
    """Read MKA tags from all tracks, deduplicate values."""
    fields = ["title", "artist", "album_artist", "composer", "genre",
              "read_by", "category", "min_age", "max_age"]
    collected: dict[str, list[str]] = {f: [] for f in fields}
    collected["track_titles"] = []

    for filename in playlist.track_files:
        track_path = playlist.path / filename
        try:
            tags = mka.read_tags(track_path)
        except Exception:
            tags = {}

        title = tags.get("title") or Path(filename).stem
        collected["track_titles"].append(title)

        for field in fields:
            if field == "title":
                continue  # handled as track_titles
            value = tags.get(field, "")
            if value and value not in collected[field]:
                collected[field].append(value)

    return collected


def _build_prompt(playlist_title: str, metadata: dict[str, list[str]]) -> str:
    """Build the prompt for Claude Haiku."""
    parts = [
        "Write a 1-2 sentence description (under 200 characters) of this children's audio playlist.",
        "The description will be used as input to an image generation model to create cover art, so focus on the central visual theme or mood.",
        "Do not include quotation marks around the description.",
        "",
        f"Playlist: {playlist_title}",
    ]

    if metadata.get("genre"):
        parts.append(f"Genre: {', '.join(metadata['genre'])}")
    if metadata.get("artist"):
        parts.append(f"Artist: {', '.join(metadata['artist'])}")
    if metadata.get("album_artist"):
        parts.append(f"Album Artist: {', '.join(metadata['album_artist'])}")
    if metadata.get("composer"):
        parts.append(f"Composer: {', '.join(metadata['composer'])}")
    if metadata.get("read_by"):
        parts.append(f"Read by: {', '.join(metadata['read_by'])}")
    if metadata.get("category"):
        parts.append(f"Category: {', '.join(metadata['category'])}")
    if metadata.get("min_age") or metadata.get("max_age"):
        age_parts = []
        if metadata.get("min_age"):
            age_parts.append(f"min {metadata['min_age'][0]}")
        if metadata.get("max_age"):
            age_parts.append(f"max {metadata['max_age'][0]}")
        parts.append(f"Age range: {', '.join(age_parts)}")

    if metadata.get("track_titles"):
        parts.append("Tracks:")
        for title in metadata["track_titles"]:
            parts.append(f"- {title}")

    return "\n".join(parts)


def _call_claude(prompt: str) -> str | None:
    """Call Claude CLI and return the response text, or None on failure."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", "haiku"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return None

        import json
        try:
            wrapper = json.loads(result.stdout)
            text = wrapper.get("result", result.stdout)
        except json.JSONDecodeError:
            text = result.stdout

        return text.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
