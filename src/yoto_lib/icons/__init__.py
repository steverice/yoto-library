"""Icon resolution for Yoto playlist tracks."""

from __future__ import annotations

import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from PIL import Image

from yoto_lib import mka
from yoto_lib.icons.download import (
    ICON_CACHE_DIR,
    download_icon,
)
from yoto_lib.icons.download import (
    _download_bytes as _download_bytes,
)
from yoto_lib.icons.download import (
    extract_icon_hash as extract_icon_hash,
)
from yoto_lib.icons.generate import (
    CANVAS_SIZE as CANVAS_SIZE,
)
from yoto_lib.icons.generate import (
    GRID_SIZE as GRID_SIZE,
)
from yoto_lib.icons.generate import (
    TILE_SIZE as TILE_SIZE,
)
from yoto_lib.icons.generate import (
    RetroDiffusionProvider as RetroDiffusionProvider,
)
from yoto_lib.icons.generate import (
    _build_pixelart_prompt as _build_pixelart_prompt,
)
from yoto_lib.icons.generate import (
    build_icon_prompt as build_icon_prompt,
)
from yoto_lib.icons.generate import (
    crop_icon_from_grid as crop_icon_from_grid,
)
from yoto_lib.icons.generate import (
    generate_raw_grid as generate_raw_grid,
)
from yoto_lib.icons.generate import (
    generate_retrodiffusion_icon as generate_retrodiffusion_icon,
)
from yoto_lib.icons.generate import (
    generate_retrodiffusion_icons,
    generate_track_icon,
)
from yoto_lib.icons.icon_catalog import get_catalog
from yoto_lib.icons.icon_llm import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    compare_icons_llm,
    describe_icons_llm,
    match_icon_llm,
)
from yoto_lib.icons.image import (
    ICNS_SIZES as ICNS_SIZES,
)
from yoto_lib.icons.image import (
    ICNS_TYPE_MAP as ICNS_TYPE_MAP,
)
from yoto_lib.icons.image import (
    ICON_SIZE as ICON_SIZE,
)
from yoto_lib.icons.image import (
    _color_distance as _color_distance,
)
from yoto_lib.icons.image import (
    _dominant_color_downscale as _dominant_color_downscale,
)
from yoto_lib.icons.image import (
    build_icns as build_icns,
)
from yoto_lib.icons.image import (
    generate_icns_sizes as generate_icns_sizes,
)
from yoto_lib.icons.image import (
    nearest_neighbor_upscale as nearest_neighbor_upscale,
)
from yoto_lib.icons.image import (
    remove_solid_background as remove_solid_background,
)
from yoto_lib.icons.macos import (
    _run_osascript as _run_osascript,
)
from yoto_lib.icons.macos import (
    apply_icon_to_mka,
    set_macos_file_icon,
)
from yoto_lib.icons.macos import (
    clear_macos_file_icon as clear_macos_file_icon,
)
from yoto_lib.mka import sanitize_filename as _sanitize_title  # noqa: F401

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from yoto_lib.playlist import Playlist
    from yoto_lib.yoto.api import YotoAPI


# ── resolve_icons ────────────────────────────────────────────────────────────


def _derive_track_title(track_path: Path, filename: str) -> str:
    """Get a human-readable title for matching: MKA tag -> filename stem."""
    title = Path(filename).stem
    try:
        tags = mka.read_tags(track_path)
        title = tags.get("title") or title
    except (OSError, subprocess.CalledProcessError):
        pass
    return title


def _upload_icon_bytes(api: YotoAPI, icon_bytes: bytes) -> str | None:
    """Upload icon bytes to Yoto API, return mediaId or None on failure."""
    logger.debug("_upload_icon_bytes: %d bytes", len(icon_bytes))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(icon_bytes)
        tmp_path = Path(tmp.name)
    try:
        upload_result = api.upload_icon(tmp_path, auto_convert=True)
        di = upload_result.get("displayIcon", upload_result)
        media_id = di.get("mediaId") or di.get("id")
        logger.debug("_upload_icon_bytes: -> mediaId=%s", media_id)
        return media_id
    except (OSError, httpx.HTTPError, KeyError, ValueError) as exc:
        logger.debug("_upload_icon_bytes: upload failed: %s", exc)
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def _pick_ai_icon(
    track_title: str,
    batch: list[tuple[bytes, Image.Image]],
    yoto_icon_bytes: bytes | None = None,
    yoto_media_id: str | None = None,
    descriptions: list[str] | None = None,
    album_description: str | None = None,
) -> tuple[bytes | None, Image.Image | None, str | None]:
    """Use LLM to pick the best icon from AI candidates (+ optional Yoto icon).

    Returns (icon_bytes, icon_image, media_id_if_yoto_won).
    media_id_if_yoto_won is set only when the Yoto icon wins; otherwise None.
    """
    if not batch:
        return None, None, None

    raw_images = [raw for raw, _ in batch]
    winner, _scores = compare_icons_llm(
        track_title,
        raw_images,
        yoto_icon=yoto_icon_bytes,
        descriptions=descriptions,
        album_description=album_description,
    )

    total = len(batch) + (1 if yoto_icon_bytes else 0)
    winner = max(1, min(winner, total))  # clamp to valid range

    if yoto_icon_bytes and winner == total:
        # Yoto icon won
        logger.debug("_pick_ai_icon: Yoto icon won for '%s'", track_title)
        yoto_img = Image.open(io.BytesIO(yoto_icon_bytes))
        return yoto_icon_bytes, yoto_img, yoto_media_id

    # AI icon won
    logger.debug("_pick_ai_icon: AI option %d won for '%s'", winner, track_title)
    idx = winner - 1
    _raw_bytes, processed_img = batch[idx]
    buf = io.BytesIO()
    processed_img.save(buf, format="PNG")
    return buf.getvalue(), processed_img, None


def _read_album_description(playlist_path: Path) -> str | None:
    """Read description.txt from a playlist folder, if it exists."""
    desc_path = playlist_path / "description.txt"
    if desc_path.exists():
        return desc_path.read_text(encoding="utf-8")
    return None


def resolve_icons(
    playlist: Playlist,
    api: YotoAPI,
    log: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Resolve and embed icons for each track in the playlist.

    Resolution order:
    1. MKA attachment named "icon" -> already on disk, upload to API
    2. LLM match against Yoto public icon catalog:
       - High confidence (>= 0.8) -> use Yoto icon directly
       - Gray zone (0.4 - 0.8) -> generate 3 AI, LLM picks best of 4
       - Low confidence (< 0.4) -> generate 3 AI, LLM picks best of 3
    3. Fallback: lexical match -> single AI generation (if LLM unavailable)

    Returns dict mapping filename -> mediaId.
    """
    _log = log or (lambda msg: None)
    result: dict[str, str] = {}
    catalog: list[dict] | None = None  # lazy-loaded
    total = len(playlist.track_files)
    logger.debug("resolve_icons: %d tracks", total)

    for i, filename in enumerate(playlist.track_files, 1):
        track_path = playlist.path / filename
        title = Path(filename).stem
        media_id: str | None = None
        icon_bytes: bytes | None = None

        # 1. Check for existing MKA icon attachment
        try:
            icon_bytes = mka.get_attachment(track_path, "icon")
        except (OSError, subprocess.CalledProcessError):
            icon_bytes = None

        if icon_bytes is not None:
            logger.debug("resolve_icons[%s]: found local MKA icon", filename)
            _log(f"Icon {i}/{total}: {title} (local)")
            media_id = _upload_icon_bytes(api, icon_bytes)
        else:
            track_title = _derive_track_title(track_path, filename)

            # Load catalog once
            if catalog is None:
                catalog = get_catalog(api)

            # Exact-match shortcut: skip LLM for obvious matches
            exact_match_id = None
            title_lower = track_title.lower()
            for icon in catalog:
                icon_title = (icon.get("title", "") or icon.get("name", "")).lower()
                if icon_title and icon_title == title_lower:
                    exact_match_id = icon.get("mediaId")
                    break

            if exact_match_id:
                logger.debug("resolve_icons[%s]: exact match -> mediaId=%s", filename, exact_match_id)
                _log(f"Icon {i}/{total}: {title} (exact match)")
                dl_bytes = download_icon(exact_match_id)
                if dl_bytes:
                    apply_icon_to_mka(track_path, dl_bytes)
                    icon_bytes = dl_bytes
                media_id = exact_match_id
                if media_id is not None:
                    if icon_bytes is not None:
                        try:
                            img = Image.open(io.BytesIO(icon_bytes))
                            set_macos_file_icon(track_path, img)
                        except OSError:
                            pass
                    result[filename] = media_id
                    continue

            # 2. LLM-based matching
            matched_id, confidence = match_icon_llm(track_title, catalog)
            logger.debug("resolve_icons[%s]: LLM match mediaId=%s confidence=%.2f", filename, matched_id, confidence)

            if matched_id and confidence >= CONFIDENCE_HIGH:
                # High confidence — use Yoto icon directly
                logger.debug("resolve_icons[%s]: high confidence, using Yoto icon", filename)
                _log(f"Icon {i}/{total}: {title} (matched, confidence: {confidence:.2f})")
                dl_bytes = download_icon(matched_id)
                if dl_bytes:
                    apply_icon_to_mka(track_path, dl_bytes)
                    icon_bytes = dl_bytes
                media_id = matched_id

            elif matched_id and confidence >= CONFIDENCE_LOW:
                # Gray zone — generate 3 AI, compare with Yoto icon
                logger.debug("resolve_icons[%s]: gray zone, generating AI candidates + Yoto", filename)
                _log(f"Icon {i}/{total}: {title} (comparing, confidence: {confidence:.2f})")
                yoto_bytes = download_icon(matched_id)
                album_desc = _read_album_description(playlist.path)
                descriptions = describe_icons_llm(track_title, album_description=album_desc)
                batch = generate_retrodiffusion_icons(descriptions) if descriptions else []

                icon_bytes_result, _icon_img, yoto_won_id = _pick_ai_icon(
                    track_title,
                    batch,
                    yoto_icon_bytes=yoto_bytes,
                    yoto_media_id=matched_id,
                    descriptions=descriptions,
                    album_description=album_desc,
                )

                if yoto_won_id:
                    _log(f"Icon {i}/{total}: {title} (compared, chose: yoto)")
                    if yoto_bytes:
                        apply_icon_to_mka(track_path, yoto_bytes)
                    icon_bytes = yoto_bytes
                    media_id = yoto_won_id
                elif icon_bytes_result:
                    _log(f"Icon {i}/{total}: {title} (compared, chose: AI)")
                    apply_icon_to_mka(track_path, icon_bytes_result)
                    icon_bytes = icon_bytes_result
                    media_id = _upload_icon_bytes(api, icon_bytes_result)
                    if media_id:
                        ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        (ICON_CACHE_DIR / f"{media_id}.png").write_bytes(icon_bytes_result)

            else:
                # Low confidence — generate 3 AI, pick best
                logger.debug("resolve_icons[%s]: low confidence, generating AI candidates", filename)
                _log(f"Icon {i}/{total}: {title} (generating...)")
                album_desc = _read_album_description(playlist.path)
                descriptions = describe_icons_llm(track_title, album_description=album_desc)
                batch = generate_retrodiffusion_icons(descriptions) if descriptions else []

                if batch:
                    icon_bytes_result, _icon_img, _ = _pick_ai_icon(
                        track_title,
                        batch,
                        descriptions=descriptions,
                        album_description=album_desc,
                    )
                    if icon_bytes_result:
                        apply_icon_to_mka(track_path, icon_bytes_result)
                        icon_bytes = icon_bytes_result
                        media_id = _upload_icon_bytes(api, icon_bytes_result)
                        if media_id:
                            ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                            (ICON_CACHE_DIR / f"{media_id}.png").write_bytes(icon_bytes_result)
                        _log(f"Icon {i}/{total}: {title} (generated)")

                if media_id is None:
                    # Fallback: old single-icon generation
                    logger.debug("resolve_icons[%s]: fallback to single generation", filename)
                    icon_bytes = generate_track_icon(track_title)
                    if icon_bytes:
                        apply_icon_to_mka(track_path, icon_bytes)
                        media_id = _upload_icon_bytes(api, icon_bytes)
                        if media_id:
                            ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                            (ICON_CACHE_DIR / f"{media_id}.png").write_bytes(icon_bytes)
                    else:
                        logger.debug("resolve_icons[%s]: no icon generated", filename)
                        _log(f"Icon {i}/{total}: {title} (no icon)")

        # Set macOS Finder icon
        if icon_bytes is not None and media_id is not None:
            try:
                img = Image.open(io.BytesIO(icon_bytes))
                set_macos_file_icon(track_path, img)
            except OSError:
                pass

        if media_id is not None:
            result[filename] = media_id

    return result
