"""Source providers — resolve .webloc URLs into audio files."""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from yoto_lib.mka import wrap_in_mka, write_tags

WORKERS = int(os.environ.get("YOTO_WORKERS", "4"))

logger = logging.getLogger(__name__)


def parse_webloc(path: Path) -> str | None:
    """Extract the URL from a .webloc plist file. Returns None on failure."""
    try:
        data = plistlib.loads(path.read_bytes())
        return data.get("URL")
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def _get_providers() -> list[Any]:
    """Return all registered source providers."""
    from yoto_lib.sources.youtube import YouTubeProvider
    return [YouTubeProvider()]


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """Return a path in directory that doesn't collide with existing files."""
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = directory / f"{stem} {n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _resolve_one_webloc(
    webloc: Path,
    playlist_dir: Path,
    trim: bool,
    providers: list[Any],
    on_track_start: Callable[[str], None] | None,
    on_download_progress: Callable[[str, float, int, int | None, str], None] | None,
) -> Path | None:
    """Resolve a single .webloc file. Returns the created .mka path, or None on failure."""
    url = parse_webloc(webloc)
    if url is None:
        logger.warning("Could not parse URL from %s, skipping", webloc.name)
        return None

    # Find a matching provider
    provider = None
    for p in providers:
        if p.can_handle(url):
            provider = p
            break

    if provider is None:
        logger.warning("No provider for URL %s in %s, skipping", url, webloc.name)
        return None

    logger.debug("resolve_weblocs: %s -> %s (provider: %s)", webloc.name, url, type(provider).__name__)

    if on_track_start:
        on_track_start(webloc.stem)

    # Build per-track progress callback
    def _on_dl_progress(pct: float, downloaded: int, total: int | None, speed: str) -> None:
        if on_download_progress:
            on_download_progress(webloc.stem, pct, downloaded, total, speed)

    # Download
    try:
        audio_path, metadata = provider.download(
            url, playlist_dir, trim=trim,
            on_progress=_on_dl_progress if on_download_progress else None,
        )
        logger.debug("resolve_weblocs: downloaded %s -> %s", webloc.name, audio_path.name)
    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        logger.warning("Download failed for %s: %s", webloc.name, exc)
        return None

    # Wrap in MKA
    title = metadata.get("title", webloc.stem)
    mka_path = _unique_path(playlist_dir, title, ".mka")
    try:
        wrap_in_mka(audio_path, mka_path)
        metadata["source_format"] = audio_path.suffix.lstrip(".").lower()
        write_tags(mka_path, metadata)
        logger.debug("resolve_weblocs: wrapped %s -> %s", audio_path.name, mka_path.name)
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning("MKA wrapping failed for %s: %s", webloc.name, exc)
        mka_path.unlink(missing_ok=True)
        return None
    finally:
        # Clean up intermediate audio file regardless of MKA outcome
        audio_path.unlink(missing_ok=True)

    # Success — consume the .webloc
    webloc.unlink()
    return mka_path


def resolve_weblocs(
    playlist_dir: Path,
    trim: bool = True,
    on_track_done: Callable[[str], None] | None = None,
    on_track_start: Callable[[str], None] | None = None,
    on_download_progress: Callable[[str, float, int, int | None, str], None] | None = None,
) -> list[Path]:
    """Resolve .webloc files in a playlist directory into .mka tracks.

    For each .webloc:
      1. Parse URL from plist
      2. Find a provider that can handle the URL
      3. Download audio (in parallel, up to WORKERS concurrent downloads)
      4. Wrap in MKA and write metadata tags
      5. Delete the .webloc

    Returns list of newly created .mka paths in original webloc sort order.
    Skips (with warning) on unrecognized URLs or download failures.

    Args:
        on_track_start: Called with the webloc stem when a download begins.
        on_download_progress: Called with (name, pct, downloaded, total, speed) during download.
        on_track_done: Called with the mka filename when a track is fully resolved.
    """
    weblocs = sorted(playlist_dir.glob("*.webloc"))
    if not weblocs:
        return []

    logger.debug("resolve_weblocs: %d .webloc files in %s", len(weblocs), playlist_dir)
    providers = _get_providers()

    # Submit all weblocs in parallel; preserve original order via index
    future_to_index: dict[Any, int] = {}
    results_by_index: dict[int, Path | None] = {}

    with ThreadPoolExecutor(max_workers=min(WORKERS, len(weblocs))) as executor:
        for idx, webloc in enumerate(weblocs):
            future = executor.submit(
                _resolve_one_webloc,
                webloc, playlist_dir, trim, providers,
                on_track_start, on_download_progress,
            )
            future_to_index[future] = idx

        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                mka_path = future.result()
            except Exception as exc:
                logger.warning("resolve_weblocs: unexpected error for index %d: %s", idx, exc)
                mka_path = None
            results_by_index[idx] = mka_path
            if mka_path is not None and on_track_done:
                on_track_done(mka_path.name)

    # Return in original order, skipping failures
    return [p for i in range(len(weblocs)) if (p := results_by_index.get(i)) is not None]
