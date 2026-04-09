"""iTunes Search API integration for album art and metadata enrichment."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

from yoto_lib.mka import _run, extract_album_art, write_tags

logger = logging.getLogger(__name__)

_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


def search_itunes_album(artist: str, album: str) -> list[dict[str, Any]]:
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
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("iTunes Search API request failed for '%s - %s': %s", artist, album, exc)
        return []


_MIN_SIMILARITY = 0.6


def _normalize(s: str) -> str:
    """Lowercase and strip punctuation for fuzzy comparison."""
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def match_album(results: list[dict[str, Any]], artist: str, album: str) -> dict[str, Any] | None:
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
            best_score,
            artist,
            album,
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


def embed_album_art(mka_path: Path, image_bytes: bytes) -> bool:
    """Embed album art as a video stream in an MKA file.

    Re-muxes the MKA with the image as an attached picture video stream.
    Returns True on success, False on failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as img_tmp:
        img_tmp.write(image_bytes)
        img_path = Path(img_tmp.name)

    out_path = mka_path.with_suffix(".tmp.mka")
    try:
        result = _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(mka_path),
                "-i",
                str(img_path),
                "-map",
                "0",
                "-map",
                "1",
                "-c",
                "copy",
                "-disposition:v:0",
                "attached_pic",
                str(out_path),
            ],
            check=False,
        )
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            logger.warning("embed_album_art: ffmpeg failed for %s", mka_path.name)
            return False

        out_path.replace(mka_path)
        return True
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning("embed_album_art: failed for %s: %s", mka_path.name, exc)
        return False
    finally:
        img_path.unlink(missing_ok=True)
        if out_path.exists():
            out_path.unlink()


# Metadata fields to backfill from iTunes API results.
# Maps API response key -> internal tag name.
_BACKFILL_FIELDS = {
    "primaryGenreName": "genre",
    "releaseDate": "date",
    "copyright": "copyright",
}

# Cache sentinel for "we looked and found nothing"
_NO_MATCH = object()


def _download_artwork(result: dict[str, Any]) -> bytes | None:
    """Download high-res artwork for an iTunes album result.

    Returns JPEG bytes, or None on failure.
    """
    artwork_api_url = result.get("artworkUrl100", "")
    if not artwork_api_url:
        return None
    art_url = _artwork_url(artwork_api_url, 1200)
    try:
        response = httpx.get(art_url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        return response.content
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Failed to download iTunes artwork: %s", exc)
        return None


def enrich_from_itunes(
    mka_path: Path,
    tags: dict[str, str],
    album_cache: dict[tuple[str, str], object],
) -> None:
    """Look up album art and metadata from iTunes Search API if the MKA lacks artwork.

    Args:
        mka_path: Path to the MKA file to enrich.
        tags: Source tags already extracted from the file (internal field names).
        album_cache: Shared dict for caching results across tracks in a single import.
            Keys are (artist, album) tuples; values are (result_dict, image_bytes)
            tuples, or _NO_MATCH.
    """
    # Only proceed if artwork is missing
    if extract_album_art(mka_path) is not None:
        return

    artist = tags.get("artist", "").strip()
    album = tags.get("album", "").strip()
    if not artist or not album:
        return

    cache_key = (artist.lower(), album.lower())

    # Check cache — stores (result_dict, image_bytes) or _NO_MATCH
    if cache_key not in album_cache:
        results = search_itunes_album(artist, album)
        matched = match_album(results, artist, album)
        if matched is None:
            album_cache[cache_key] = _NO_MATCH
        else:
            image_bytes = _download_artwork(matched)
            album_cache[cache_key] = (matched, image_bytes)

    cached = album_cache[cache_key]
    if cached is _NO_MATCH:
        return

    result, image_bytes = cached

    # Embed artwork
    if image_bytes is not None:
        if embed_album_art(mka_path, image_bytes):
            logger.info(
                "Embedded iTunes artwork in %s (%s - %s)",
                mka_path.name,
                artist,
                album,
            )

    # Backfill missing metadata
    backfill = {}
    for api_key, tag_name in _BACKFILL_FIELDS.items():
        if tag_name not in tags and api_key in result:
            backfill[tag_name] = result[api_key]

    if backfill:
        write_tags(mka_path, backfill)
        logger.debug("Backfilled metadata for %s: %s", mka_path.name, list(backfill.keys()))
