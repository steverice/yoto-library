"""Icon download, caching, and hash extraction."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

ICON_BASE_URL = "https://media-secure-v2.api.yotoplay.com/icons"
ICON_CACHE_DIR = Path.home() / ".cache" / "yoto" / "icons"


# ── Download helpers ─────────────────────────────────────────────────────────


def _download_bytes(url: str) -> bytes:
    """Fetch raw bytes from a URL."""
    response = httpx.get(url, follow_redirects=True, timeout=300.0)
    response.raise_for_status()
    return response.content


def extract_icon_hash(icon_ref: str) -> str | None:
    """Extract icon hash from either 'yoto:#hash' or a full URL."""
    if not icon_ref:
        return None
    if icon_ref.startswith("yoto:#"):
        return icon_ref[6:]
    return icon_ref.rstrip("/").rsplit("/", 1)[-1] or None


def download_icon(icon_ref: str, cache_dir: Path = ICON_CACHE_DIR) -> bytes | None:
    """Download an icon by ref (yoto:#hash, URL, or bare mediaId), using file cache."""
    icon_hash = extract_icon_hash(icon_ref) if ":" in icon_ref or "/" in icon_ref else icon_ref
    if not icon_hash:
        return None

    cached = cache_dir / f"{icon_hash}.png"
    if cached.exists():
        logger.debug("download_icon: cache hit for %s", icon_hash)
        return cached.read_bytes()

    url = icon_ref if icon_ref.startswith("http") else f"{ICON_BASE_URL}/{icon_hash}"

    try:
        logger.debug("download_icon: fetching %s", icon_hash)
        data = _download_bytes(url)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data
    except (OSError, httpx.HTTPError):
        return None
