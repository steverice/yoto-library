"""Pull engine: download remote Yoto playlist to local folder."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from yoto_lib.config import WORKERS

logger = logging.getLogger(__name__)

from yoto_lib.icons import ICON_CACHE_DIR, apply_icon_to_mka, download_icon
from yoto_lib.mka import sanitize_filename as _sanitize_filename
from yoto_lib.mka import wrap_in_mka
from yoto_lib.playlist import write_jsonl
from yoto_lib.yoto.api import YotoAPI


@dataclass
class PullResult:
    card_id: str | None = None
    tracks_downloaded: int = 0
    icons_downloaded: int = 0
    cover_downloaded: bool = False
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


def _download_file(
    url: str,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> bytes:
    """Download url and return content as bytes.

    Args:
        on_progress: Optional callback invoked with (downloaded_bytes, total_bytes_or_None)
            after each chunk is received.
    """
    chunks: list[bytes] = []
    with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as response:
        response.raise_for_status()
        total: int | None = None
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                total = int(content_length)
            except ValueError:
                pass
        downloaded = 0
        for chunk in response.iter_bytes(chunk_size=65536):
            chunks.append(chunk)
            downloaded += len(chunk)
            if on_progress:
                on_progress(downloaded, total)
    return b"".join(chunks)


@dataclass
class _TrackJob:
    """All info needed to download and process one track."""

    title: str
    filename: str
    track_url: str
    icon_ref: str


def _process_track(
    job: _TrackJob,
    folder: Path,
    cache_dir: Path,
    on_progress: Callable[[int, int | None], None] | None = None,
    on_track_start: Callable[[str], None] | None = None,
) -> tuple[bool, bool, str | None]:
    """Download, wrap in MKA, and apply icon for one track.

    Returns (track_ok, icon_ok, error_message).

    Args:
        on_progress: Optional callback passed to _download_file for byte-level progress.
        on_track_start: Optional callback invoked at the start of processing this track.
    """
    if on_track_start:
        on_track_start(job.title)
    safe_name = _sanitize_filename(job.title)
    mka_path = folder / job.filename
    raw_path = folder / f".{safe_name}.raw"
    error = None
    track_ok = False
    icon_ok = False

    try:
        logger.debug("pull: downloading track '%s'", job.title)
        audio_data = _download_file(job.track_url, on_progress=on_progress)
        logger.debug("pull: downloaded '%s' (%d bytes)", job.title, len(audio_data))
        raw_path.write_bytes(audio_data)
        wrap_in_mka(raw_path, mka_path)
        raw_path.unlink(missing_ok=True)
        track_ok = True
    except (OSError, httpx.HTTPError, subprocess.CalledProcessError) as exc:
        raw_path.unlink(missing_ok=True)
        return False, False, f"Failed to download {job.title}: {exc}"

    if job.icon_ref:
        try:
            logger.debug("pull: applying icon for '%s' (ref=%s)", job.title, job.icon_ref)
            icon_data = download_icon(job.icon_ref, cache_dir)
            if icon_data:
                apply_icon_to_mka(mka_path, icon_data)
                icon_ok = True
        except (OSError, httpx.HTTPError, subprocess.CalledProcessError) as exc:
            error = f"Failed to set icon for {job.title}: {exc}"

    return track_ok, icon_ok, error


def pull_playlist(
    folder: Path,
    card_id: str | None = None,
    dry_run: bool = False,
    on_track_done: Callable[[str], None] | None = None,
    on_total: Callable[[int], None] | None = None,
    on_track_start: Callable[[str], None] | None = None,
    on_download_progress: Callable[[str, int, int | None], None] | None = None,
) -> PullResult:
    """Download a remote Yoto playlist into a local folder."""
    folder = Path(folder)
    result = PullResult(dry_run=dry_run)
    logger.debug("pull: folder=%s card_id=%s dry_run=%s", folder, card_id, dry_run)

    # Determine card ID
    card_id_path = folder / ".yoto-card-id"
    if card_id is None and card_id_path.exists():
        card_id = card_id_path.read_text(encoding="utf-8").strip()
        logger.debug("pull: resolved card_id=%s from .yoto-card-id", card_id)

    if card_id is None:
        result.errors.append("No card ID provided and no .yoto-card-id file found.")
        return result

    result.card_id = card_id

    api = YotoAPI()
    remote = api.get_content(card_id, playable=True)

    if dry_run:
        logger.debug("pull: dry run, returning early")
        return result

    folder.mkdir(parents=True, exist_ok=True)
    card_id_path.write_text(card_id, encoding="utf-8")

    # Description
    description = remote.get("metadata", {}).get("description", "")
    if description:
        (folder / "description.txt").write_text(description, encoding="utf-8")

    # Cover
    cover_url = remote.get("metadata", {}).get("cover", {}).get("imageL")
    if cover_url:
        try:
            logger.debug("pull: downloading cover")
            (folder / "cover.png").write_bytes(_download_file(cover_url))
            result.cover_downloaded = True
        except (OSError, httpx.HTTPError) as exc:
            result.errors.append(f"Failed to download cover: {exc}")

    # Build track jobs (preserving chapter order)
    chapters = remote.get("content", {}).get("chapters", [])
    logger.debug("pull: %d chapters in remote content", len(chapters))
    jobs: list[_TrackJob] = []
    for chapter in chapters:
        icon_ref = chapter.get("display", {}).get("icon16x16", "")
        for track in chapter.get("tracks", []):
            track_url = track.get("trackUrl", "")
            if not track_url.startswith("http"):
                continue
            title = track.get("title") or chapter.get("title") or chapter.get("key") or "track"
            safe_name = _sanitize_filename(title)
            jobs.append(
                _TrackJob(
                    title=title,
                    filename=f"{safe_name}.mka",
                    track_url=track_url,
                    icon_ref=icon_ref,
                )
            )

    if on_total:
        on_total(len(jobs))

    # Process tracks in parallel
    logger.debug("pull: %d tracks to download", len(jobs))
    cache_dir = ICON_CACHE_DIR
    future_to_job = {}
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(jobs)) if jobs else 1) as executor:
        for job in jobs:
            # Build per-track progress callback
            def _make_progress_cb(title: str) -> Callable[[int, int | None], None] | None:
                if on_download_progress is None:
                    return None

                def _cb(downloaded: int, total: int | None) -> None:
                    on_download_progress(title, downloaded, total)

                return _cb

            future = executor.submit(
                _process_track,
                job,
                folder,
                cache_dir,
                _make_progress_cb(job.title),
                on_track_start,
            )
            future_to_job[future] = job

        for future in as_completed(future_to_job):
            job = future_to_job[future]
            track_ok, icon_ok, error = future.result()
            if track_ok:
                result.tracks_downloaded += 1
            if icon_ok:
                result.icons_downloaded += 1
            if error:
                result.errors.append(error)
            if on_track_done:
                on_track_done(job.title)

    # Write playlist.jsonl in original chapter order
    track_filenames = [job.filename for job in jobs]
    if track_filenames:
        write_jsonl(folder / "playlist.jsonl", track_filenames)

    return result
