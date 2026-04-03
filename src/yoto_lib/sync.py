"""Sync engine — orchestrates local → remote for Yoto playlists."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from yoto_lib.api import YotoAPI
from yoto_lib.cover import generate_cover_if_missing
from yoto_lib.icons import resolve_icons
from yoto_lib.playlist import (
    AUDIO_EXTENSIONS,
    Playlist,
    build_content_schema,
    diff_playlists,
    load_playlist,
)


# ── SyncResult ────────────────────────────────────────────────────────────────


@dataclass
class SyncResult:
    card_id: Optional[str] = None
    tracks_uploaded: int = 0
    cover_uploaded: bool = False
    icons_uploaded: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


# ── _parse_remote_state ───────────────────────────────────────────────────────


def _parse_remote_state(remote_content: dict) -> dict:
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

    has_cover = bool(
        remote_content.get("metadata", {}).get("cover", {}).get("imageL")
    )
    description = remote_content.get("description", None)

    return {
        "tracks": tracks,
        "description": description,
        "has_cover": has_cover,
        "track_hashes": track_hashes,
    }


# ── sync_playlist ─────────────────────────────────────────────────────────────


def sync_playlist(
    folder: Path,
    dry_run: bool = False,
    on_track_done: Optional[Callable[[str], None]] = None,
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
    result = SyncResult(dry_run=dry_run)

    # 1. Load local playlist
    playlist = load_playlist(folder)
    result.card_id = playlist.card_id

    # 2. Create API client
    api = YotoAPI()

    # 3. Fetch remote state
    remote_state: Optional[dict] = None
    remote_track_hashes: dict[str, str] = {}

    if playlist.card_id:
        try:
            remote_content = api.get_content(playlist.card_id)
            remote_state = _parse_remote_state(remote_content)
            remote_track_hashes = remote_state.get("track_hashes", {})
        except Exception as exc:
            result.errors.append(f"Failed to fetch remote state: {exc}")

    # 4. Diff
    diff = diff_playlists(playlist, remote_state)

    # 5. Generate cover if missing
    generate_cover_if_missing(playlist)

    # Reload has_cover after potential generation
    playlist.has_cover = playlist.cover_path.exists()

    # 6. Resolve icons
    icon_ids: dict[str, str] = resolve_icons(playlist, api)
    result.icons_uploaded = len(icon_ids)

    # Counts for dry_run reporting
    result.tracks_uploaded = len(diff.new_tracks)
    result.cover_uploaded = diff.cover_changed and playlist.has_cover

    # 7. Return early if dry_run
    if dry_run:
        return result

    # 8. Build track_hashes: reuse remote hashes for existing, upload new
    track_hashes: dict[str, str] = {}

    # Reuse hashes for tracks not in the diff
    for filename in playlist.track_files:
        if filename not in diff.new_tracks and filename in remote_track_hashes:
            track_hashes[filename] = remote_track_hashes[filename]

    # 9. Upload new tracks
    for filename in diff.new_tracks:
        file_path = folder / filename
        if not file_path.exists():
            result.errors.append(f"Track file not found: {filename}")
            continue
        try:
            transcode_result = api.upload_and_transcode(file_path)
            sha = transcode_result.get("transcodedSha256", "")
            track_hashes[filename] = sha
            if on_track_done:
                on_track_done(filename)
        except Exception as exc:
            result.errors.append(f"Upload failed for {filename}: {exc}")
            if on_track_done:
                on_track_done(filename)

    # 10. Upload cover if changed
    cover_url: Optional[str] = None
    if diff.cover_changed and playlist.has_cover:
        try:
            cover_result = api.upload_cover(playlist.cover_path)
            cover_url = cover_result.get("url") or cover_result.get("coverUrl")
            result.cover_uploaded = True
        except Exception as exc:
            result.errors.append(f"Cover upload failed: {exc}")
            result.cover_uploaded = False

    # 11. Build content schema and POST
    schema = build_content_schema(playlist, track_hashes, icon_ids, cover_url)
    try:
        response = api.create_or_update_content(schema)
        new_card_id: Optional[str] = response.get("cardId") or response.get(
            "content", {}
        ).get("cardId")
        if new_card_id:
            result.card_id = new_card_id
            # 12. Write cardId to .yoto-card-id if new
            if not playlist.card_id:
                playlist.card_id_path.write_text(new_card_id, encoding="utf-8")
    except Exception as exc:
        result.errors.append(f"Content POST failed: {exc}")

    return result


# ── sync_path ─────────────────────────────────────────────────────────────────


def _has_audio_files(folder: Path) -> bool:
    """Return True if folder contains any audio files directly."""
    return any(
        p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        for p in folder.iterdir()
    )


def sync_path(
    path: Path,
    dry_run: bool = False,
    on_track_done: Optional[Callable[[str], None]] = None,
) -> list[SyncResult]:
    """
    Sync one or more playlists rooted at path.

    - If path itself contains audio files → treat as a single playlist.
    - If path contains subdirectories with audio files → sync each subdir.
    """
    path = Path(path)
    results: list[SyncResult] = []

    if _has_audio_files(path):
        results.append(sync_playlist(path, dry_run=dry_run, on_track_done=on_track_done))
    else:
        subdirs = sorted(p for p in path.iterdir() if p.is_dir())
        for subdir in subdirs:
            if _has_audio_files(subdir):
                results.append(sync_playlist(subdir, dry_run=dry_run, on_track_done=on_track_done))

    return results
