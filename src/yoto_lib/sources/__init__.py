"""Source providers — resolve .webloc URLs into audio files."""

from __future__ import annotations

import logging
import plistlib
from pathlib import Path

from yoto_lib.mka import wrap_in_mka, write_tags

logger = logging.getLogger(__name__)


def parse_webloc(path: Path) -> str | None:
    """Extract the URL from a .webloc plist file. Returns None on failure."""
    try:
        data = plistlib.loads(path.read_bytes())
        return data.get("URL")
    except Exception:
        return None


def _get_providers() -> list:
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


def resolve_weblocs(playlist_dir: Path, trim: bool = True) -> list[Path]:
    """Resolve .webloc files in a playlist directory into .mka tracks.

    For each .webloc:
      1. Parse URL from plist
      2. Find a provider that can handle the URL
      3. Download audio
      4. Wrap in MKA and write metadata tags
      5. Delete the .webloc

    Returns list of newly created .mka paths.
    Skips (with warning) on unrecognized URLs or download failures.
    """
    weblocs = sorted(playlist_dir.glob("*.webloc"))
    if not weblocs:
        return []

    providers = _get_providers()
    created: list[Path] = []

    for webloc in weblocs:
        url = parse_webloc(webloc)
        if url is None:
            logger.warning("Could not parse URL from %s, skipping", webloc.name)
            continue

        # Find a matching provider
        provider = None
        for p in providers:
            if p.can_handle(url):
                provider = p
                break

        if provider is None:
            logger.warning("No provider for URL %s in %s, skipping", url, webloc.name)
            continue

        # Download
        try:
            audio_path, metadata = provider.download(url, playlist_dir, trim=trim)
        except Exception as exc:
            logger.warning("Download failed for %s: %s", webloc.name, exc)
            continue

        # Wrap in MKA
        title = metadata.get("title", webloc.stem)
        mka_path = _unique_path(playlist_dir, title, ".mka")
        try:
            wrap_in_mka(audio_path, mka_path)
            write_tags(mka_path, metadata)
        except Exception as exc:
            logger.warning("MKA wrapping failed for %s: %s", webloc.name, exc)
            mka_path.unlink(missing_ok=True)
            continue
        finally:
            # Clean up downloaded audio (now wrapped in MKA)
            audio_path.unlink(missing_ok=True)

        # Success — consume the .webloc
        webloc.unlink()
        created.append(mka_path)

    return created
