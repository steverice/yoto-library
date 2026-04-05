"""iTunes Search API integration for album art and metadata enrichment."""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from pathlib import Path
import re
import tempfile

import httpx

from yoto_lib.mka import extract_album_art, write_tags, _run

logger = logging.getLogger(__name__)

_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


def search_itunes_album(artist: str, album: str) -> list[dict]:
    """Query iTunes Search API for albums matching artist and album name.

    Returns list of album result dicts, or empty list on failure.
    """
    try:
        response = httpx.get(
            _ITUNES_SEARCH_URL,
            params={"term": f"{artist} {album}", "entity": "album", "limit": "5"},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except Exception:
        logger.warning("iTunes Search API request failed for '%s - %s'", artist, album)
        return []


_MIN_SIMILARITY = 0.6


def _normalize(s: str) -> str:
    """Lowercase and strip punctuation for fuzzy comparison."""
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def match_album(results: list[dict], artist: str, album: str) -> dict | None:
    """Pick the best matching album from iTunes Search results.

    Returns the best result dict, or None if no result meets the similarity threshold.
    """
    if not results:
        return None

    norm_artist = _normalize(artist)
    norm_album = _normalize(album)

    best_score = 0.0
    best_result = None

    for result in results:
        api_artist = _normalize(result.get("artistName", ""))
        api_album = _normalize(result.get("collectionName", ""))

        artist_score = SequenceMatcher(None, norm_artist, api_artist).ratio()
        album_score = SequenceMatcher(None, norm_album, api_album).ratio()

        # Weight album name more heavily — artist matches are often partial
        combined = artist_score * 0.4 + album_score * 0.6

        if combined > best_score:
            best_score = combined
            best_result = result

    if best_score < _MIN_SIMILARITY:
        logger.debug(
            "No iTunes match above threshold (best=%.2f) for '%s - %s'",
            best_score, artist, album,
        )
        return None

    logger.debug(
        "iTunes match (score=%.2f): '%s' by '%s'",
        best_score,
        best_result.get("collectionName"),
        best_result.get("artistName"),
    )
    return best_result


def _artwork_url(api_url: str, size: int = 1200) -> str:
    """Rewrite an iTunes artwork URL to request a specific resolution.

    iTunes URLs end with e.g. '100x100bb.jpg'; replace with '{size}x{size}bb.jpg'.
    """
    replaced = re.sub(r"/\d+x\d+bb\.jpg$", f"/{size}x{size}bb.jpg", api_url)
    return replaced
