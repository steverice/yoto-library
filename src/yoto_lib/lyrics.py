"""Lyrics fetch pipeline: source tags first, LRCLIB API fallback."""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

_LRCLIB_BASE = "https://lrclib.net/api"
_USER_AGENT = "yoto-library/1.0 (https://github.com/smrice/yoto-library)"


def read_lyrics_from_tags(tags: dict[str, str]) -> str | None:
    """Extract lyrics from a source tags dict, if present and non-empty."""
    lyrics = tags.get("lyrics", "")
    return lyrics if lyrics.strip() else None


def _strip_lrc_timestamps(synced: str) -> str:
    """Remove [mm:ss.xx] timestamps from LRC-format synced lyrics."""
    return re.sub(r"\[\d{2}:\d{2}\.\d{2,3}\]\s*", "", synced).strip()


def fetch_lyrics_lrclib(artist: str, title: str) -> str | None:
    """Fetch lyrics from LRCLIB API by artist and title.

    Returns plain lyrics text, or None if not found.
    Prefers plainLyrics; falls back to syncedLyrics with timestamps stripped.
    """
    try:
        response = httpx.get(
            f"{_LRCLIB_BASE}/search",
            params={"artist_name": artist, "track_name": title},
            headers={"User-Agent": _USER_AGENT},
            timeout=10.0,
        )
        response.raise_for_status()
        results = response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("LRCLIB request failed for '%s - %s': %s", artist, title, exc)
        return None

    if not results:
        return None

    first = results[0]
    plain = first.get("plainLyrics")
    if plain and plain.strip():
        return plain

    synced = first.get("syncedLyrics")
    if synced and synced.strip():
        return _strip_lrc_timestamps(synced)

    return None


def get_lyrics(tags: dict[str, str]) -> tuple[str | None, str]:
    """Get lyrics from source tags or LRCLIB API.

    Returns (lyrics_text, source) where source is "tags", "lrclib", or "none".
    """
    # Try source tags first
    text = read_lyrics_from_tags(tags)
    if text:
        return text, "tags"

    # Fall back to LRCLIB API (needs artist + title)
    artist = tags.get("artist", "").strip()
    title = tags.get("title", "").strip()
    if not artist or not title:
        return None, "none"

    text = fetch_lyrics_lrclib(artist, title)
    if text:
        return text, "lrclib"

    return None, "none"
