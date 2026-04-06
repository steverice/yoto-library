"""Sync engine — orchestrates local → remote for Yoto playlists."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

from yoto_lib.api import YotoAPI
from yoto_lib.cover import generate_cover_if_missing
from yoto_lib.description import generate_description
from yoto_lib.icons import resolve_icons
from yoto_lib.sources import resolve_weblocs
from yoto_lib.playlist import (
    AUDIO_EXTENSIONS,
    Playlist,
    _title_from_filename,
    build_content_schema,
    diff_playlists,
    load_playlist,
)


# ── SyncResult ────────────────────────────────────────────────────────────────


@dataclass
class SyncResult:
    card_id: str | None = None
    tracks_uploaded: int = 0
    cover_uploaded: bool = False
    icons_uploaded: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


# ── _parse_remote_state ───────────────────────────────────────────────────────


def _parse_remote_state(remote_content: dict[str, Any]) -> dict[str, Any]:
    """
    Extract a normalised remote-state dict from a GET /content/<id> response.

    Returns:
      {
        "tracks": list[str],          # chapter titles in order
        "description": str | None,
        "has_cover": bool,
        "track_hashes": dict[str, str],   # chapter_title -> sha256 hex
      }
    """
    content = remote_content.get("content", {})
    chapters = content.get("chapters", [])

    tracks: list[str] = []
    track_hashes: dict[str, str] = {}
    track_info: dict[str, dict] = {}  # title -> {format, channels}

    # Chapters may be a list (from API) or dict (from our uploads)
    items = chapters.items() if isinstance(chapters, dict) else (
        (ch.get("key", str(i)), ch) for i, ch in enumerate(chapters)
    )

    for key, chapter in items:
        title = chapter.get("title", key).strip()
        tracks.append(title)
        for track in chapter.get("tracks", []):
            url: str = track.get("trackUrl", "")
            m = re.match(r"yoto:#(.+)", url)
            if m:
                track_hashes[title] = m.group(1)
            fmt = track.get("format", "")
            channels = track.get("channels", "")
            if fmt or channels:
                track_info[title] = {"format": fmt, "channels": channels}

    cover_url = remote_content.get("metadata", {}).get("cover", {}).get("imageL")
    has_cover = bool(cover_url)
    description = remote_content.get("description", None)

    return {
        "tracks": tracks,
        "description": description,
        "has_cover": has_cover,
        "cover_url": cover_url,
        "track_hashes": track_hashes,
        "track_info": track_info,
    }


def _infer_track_info(file_path: Path) -> dict[str, str]:
    """Infer format and channels from a local audio file via ffprobe.

    Fallback for reused tracks missing remote format info (legacy cards).
    Reports the source codec, which may differ from the server's transcoded format.
    """
    try:
        from yoto_lib.mka import probe_audio
        info = probe_audio(file_path)
        audio_streams = [s for s in info["streams"] if s.get("codec_type") == "audio"]
        if audio_streams:
            stream = audio_streams[0]
            codec = stream.get("codec_name", "")
            channels = stream.get("channels", 0)
            return {
                "format": codec,
                "channels": "stereo" if channels >= 2 else "mono",
            }
    except (ImportError, OSError, subprocess.CalledProcessError, KeyError, ValueError):
        pass
    return {}


# ── sync_playlist ─────────────────────────────────────────────────────────────


def sync_playlist(
    folder: Path,
    dry_run: bool = False,
    trim: bool = True,
    on_track_done: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> SyncResult:
    """
    Sync a single playlist folder to the Yoto API.

    Steps:
      1. load_playlist(folder)
      2. Create YotoAPI()
      3. Fetch remote state if card_id exists
      4. diff_playlists(playlist, remote)
      5. generate_cover_if_missing(playlist)
      6. resolve_icons(playlist, api) -> icon_ids
      7. If dry_run: return result with counts, no uploads
      8. Reuse remote track hashes for existing tracks
      9. Upload new tracks via api.upload_and_transcode
      10. Upload cover if changed via api.upload_cover
      11. Build content schema and POST via api.create_or_update_content
      12. Write cardId to .yoto-card-id if new
    """
    folder = Path(folder)
    logger.debug("sync: starting for %s (dry_run=%s)", folder, dry_run)

    # 0. Resolve .webloc files into .mka tracks
    resolve_weblocs(folder, trim=trim)

    result = SyncResult(dry_run=dry_run)

    # 1. Load local playlist
    playlist = load_playlist(folder)
    result.card_id = playlist.card_id
    logger.debug("sync: loaded playlist '%s' (%d tracks, card_id=%s)", playlist.title, len(playlist.track_files), playlist.card_id)

    # 2. Create API client
    api = YotoAPI()

    # 3. Fetch remote state
    remote_state: dict[str, Any] | None = None
    remote_track_hashes: dict[str, str] = {}
    remote_track_info: dict[str, dict] = {}  # title -> {format, channels}

    if playlist.card_id:
        try:
            remote_content = api.get_content(playlist.card_id)
            remote_state = _parse_remote_state(remote_content)
            remote_track_hashes = remote_state.get("track_hashes", {})
            remote_track_info = remote_state.get("track_info", {})
            logger.debug("sync: fetched remote state for %s (%d tracks)", playlist.card_id, len(remote_state.get("tracks", [])))
        except (OSError, httpx.HTTPError) as exc:
            logger.error("sync: failed to fetch remote state: %s", exc)
            result.errors.append(f"Failed to fetch remote state: {exc}")
    else:
        logger.debug("sync: no card_id, will create new card")

    # 4. Diff
    diff = diff_playlists(playlist, remote_state)
    logger.debug("sync: diff — new=%d removed=%d order_changed=%s cover_changed=%s",
                  len(diff.new_tracks), len(diff.removed_tracks), diff.order_changed, diff.cover_changed)

    _log = log or (lambda msg: None)

    # 4b. Generate description if missing
    generate_description(playlist, log=_log)
    # Reload description after potential generation
    if playlist.description is None and playlist.description_path.exists():
        playlist.description = playlist.description_path.read_text(encoding="utf-8")

    # 5. Generate cover if missing
    if not playlist.has_cover:
        _log("Generating cover image...")
    generate_cover_if_missing(playlist, log=_log)

    # Reload has_cover after potential generation
    playlist.has_cover = playlist.cover_path.exists()

    # 6. Resolve icons
    logger.debug("sync: resolving icons for %d tracks", len(playlist.track_files))
    icon_ids: dict[str, str] = resolve_icons(playlist, api, log=_log)
    result.icons_uploaded = len(icon_ids)
    logger.debug("sync: resolved %d icons", len(icon_ids))

    # Counts for dry_run reporting
    result.cover_uploaded = diff.cover_changed and playlist.has_cover

    # 7. Return early if dry_run
    if dry_run:
        result.tracks_uploaded = len(diff.new_tracks)
        logger.debug("sync: dry run complete, would upload %d tracks", len(diff.new_tracks))
        return result

    # 8. Build track_hashes and track_info: reuse remote for existing, upload new
    track_hashes: dict[str, str] = {}
    track_info: dict[str, dict] = {}  # filename -> {format, channels}
    tracks_to_upload: list[str] = list(diff.new_tracks)

    # Reuse hashes and track info for existing tracks (remote keys are titles, local keys are filenames)
    for filename in playlist.track_files:
        if filename not in diff.new_tracks:
            title = _title_from_filename(filename)
            sha = remote_track_hashes.get(title, "")
            if sha:
                track_hashes[filename] = sha
                if title in remote_track_info:
                    track_info[filename] = remote_track_info[title]
                else:
                    # Infer format from local file for tracks missing remote info
                    track_info[filename] = _infer_track_info(folder / filename)
            else:
                tracks_to_upload.append(filename)

    result.tracks_uploaded = len(tracks_to_upload)
    logger.debug("sync: uploading %d tracks (%d reused from remote)",
                  len(tracks_to_upload), len(playlist.track_files) - len(tracks_to_upload))

    # 9. Upload new tracks (and existing tracks with missing hashes)
    total_new = len(tracks_to_upload)
    for i, filename in enumerate(tracks_to_upload, 1):
        file_path = folder / filename
        if not file_path.exists():
            result.errors.append(f"Track file not found: {filename}")
            continue
        try:
            _log(f"Uploading track {i}/{total_new}: {Path(filename).stem}")
            transcode_result = api.upload_and_transcode(file_path)
            sha = transcode_result.get("transcodedSha256", "")
            track_hashes[filename] = sha
            # Extract format and channels from transcoded output for content schema
            transcoded_info = transcode_result.get("transcodedInfo", {})
            track_info[filename] = {
                "format": transcoded_info.get("format", ""),
                "channels": transcoded_info.get("channels", ""),
            }
            if on_track_done:
                on_track_done(filename)
        except (OSError, httpx.HTTPError) as exc:
            logger.error("sync: upload failed for %s: %s", filename, exc)
            result.errors.append(f"Upload failed for {filename}: {exc}")
            if on_track_done:
                on_track_done(filename)

    # 10. Upload cover if changed, or preserve existing remote cover URL
    cover_url: str | None = None
    if diff.cover_changed and playlist.has_cover:
        logger.debug("sync: uploading cover")
        _log("Uploading cover...")
        try:
            cover_result = api.upload_cover(playlist.cover_path)
            ci = cover_result.get("coverImage", cover_result)
            cover_url = ci.get("mediaUrl") or ci.get("url") or cover_result.get("coverUrl")
            result.cover_uploaded = True
        except (OSError, httpx.HTTPError) as exc:
            result.errors.append(f"Cover upload failed: {exc}")
            result.cover_uploaded = False
    elif remote_state and remote_state.get("cover_url"):
        # Preserve existing remote cover URL so POST doesn't remove it
        cover_url = remote_state["cover_url"]
        logger.debug("sync: preserving existing cover URL")

    # 11. Build content schema and POST
    logger.debug("sync: POSTing content schema")
    _log("Saving playlist to Yoto...")
    schema = build_content_schema(playlist, track_hashes, icon_ids, cover_url, track_info)
    try:
        response = api.create_or_update_content(schema)
        new_card_id: str | None = response.get("cardId") or response.get(
            "content", {}
        ).get("cardId")
        if new_card_id:
            result.card_id = new_card_id
            logger.debug("sync: saved card_id=%s", new_card_id)
            # 12. Write cardId to .yoto-card-id if new
            if not playlist.card_id:
                playlist.card_id_path.write_text(new_card_id, encoding="utf-8")
    except (OSError, httpx.HTTPError) as exc:
        logger.error("sync: content POST failed: %s", exc)
        result.errors.append(f"Content POST failed: {exc}")

    return result


# ── sync_path ─────────────────────────────────────────────────────────────────


def _has_audio_files(folder: Path) -> bool:
    """Return True if folder contains any audio files or .webloc files directly."""
    return any(
        p.is_file() and (p.suffix.lower() in AUDIO_EXTENSIONS or p.suffix.lower() == ".webloc")
        for p in folder.iterdir()
    )


def sync_path(
    path: Path,
    dry_run: bool = False,
    trim: bool = True,
    on_track_done: Callable[[str], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[SyncResult]:
    """
    Sync one or more playlists rooted at path.

    - If path itself contains audio files → treat as a single playlist.
    - If path contains subdirectories with audio files → sync each subdir.
    """
    path = Path(path)
    results: list[SyncResult] = []

    if _has_audio_files(path):
        logger.debug("sync_path: %s is a playlist folder", path)
        results.append(sync_playlist(path, dry_run=dry_run, trim=trim, on_track_done=on_track_done, log=log))
    else:
        subdirs = sorted(p for p in path.iterdir() if p.is_dir())
        playlist_dirs = [s for s in subdirs if _has_audio_files(s)]
        logger.debug("sync_path: %s contains %d playlist subdirs", path, len(playlist_dirs))
        for subdir in playlist_dirs:
            results.append(sync_playlist(subdir, dry_run=dry_run, trim=trim, on_track_done=on_track_done, log=log))

    return results
