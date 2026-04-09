"""Local cache for the Yoto public icon catalog."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from yoto_lib.api import YotoAPI

CATALOG_FILENAME = "catalog.json"
CATALOG_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# Same default as icons.py — duplicated to avoid circular import
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "yoto" / "icons"


def save_catalog(icons: list[dict], cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
    """Write the icon catalog to disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = {"fetched_at": time.time(), "icons": icons}
    (cache_dir / CATALOG_FILENAME).write_text(json.dumps(data), encoding="utf-8")


def load_catalog(cache_dir: Path = _DEFAULT_CACHE_DIR) -> list[dict] | None:
    """Read the icon catalog from disk. Returns None if not found."""
    catalog_path = cache_dir / CATALOG_FILENAME
    if not catalog_path.exists():
        return None
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        return data.get("icons")
    except (json.JSONDecodeError, KeyError):
        return None


def is_catalog_stale(cache_dir: Path = _DEFAULT_CACHE_DIR) -> bool:
    """Check if the catalog is missing or older than the TTL."""
    catalog_path = cache_dir / CATALOG_FILENAME
    if not catalog_path.exists():
        return True
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        fetched_at = data.get("fetched_at", 0)
        return (time.time() - fetched_at) > CATALOG_TTL_SECONDS
    except (json.JSONDecodeError, KeyError):
        return True


_JUNK_TITLE = re.compile(r"_test$|^MYO_radio", re.IGNORECASE)


def _filter_catalog(icons: list[dict]) -> list[dict]:
    """Remove test entries, empty titles, and duplicates from the catalog."""
    seen_titles: set[str] = set()
    filtered = []
    for icon in icons:
        title = (icon.get("title", "") or icon.get("name", "")).strip()
        if not title:
            continue
        if _JUNK_TITLE.search(title):
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        filtered.append(icon)
    return filtered


def refresh_catalog(
    api: YotoAPI,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> list[dict]:
    """Fetch the catalog from the API, save it, and download missing PNGs."""
    from yoto_lib.icons.download import download_icon

    icons = _filter_catalog(api.get_public_icons())
    logger.debug("refresh_catalog: fetched %d icons from API", len(icons))
    save_catalog(icons, cache_dir)

    # Download any icon PNGs not already cached
    for icon in icons:
        media_id = icon.get("mediaId", "")
        if not media_id:
            continue
        if (cache_dir / f"{media_id}.png").exists():
            continue
        download_icon(media_id, cache_dir)

    return icons


def get_catalog(
    api: YotoAPI | None = None,
    cache_dir: Path = _DEFAULT_CACHE_DIR,
) -> list[dict]:
    """Get the icon catalog, refreshing from API if stale.

    If API is unavailable and a stale cache exists, uses the stale cache.
    Returns an empty list if no catalog is available at all.
    """
    stale = is_catalog_stale(cache_dir)
    logger.debug("get_catalog: stale=%s", stale)
    if not stale:
        icons = load_catalog(cache_dir)
        if icons is not None:
            logger.debug("get_catalog: loaded %d icons from cache", len(icons))
            return icons

    # Need to refresh
    if api is not None:
        try:
            return refresh_catalog(api, cache_dir)
        except (OSError, httpx.HTTPError):
            pass

    # Fallback to stale cache
    icons = load_catalog(cache_dir)
    return icons if icons is not None else []
