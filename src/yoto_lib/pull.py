"""Pull engine: download remote Yoto playlist to local folder."""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx
from PIL import Image

from yoto_lib.api import YotoAPI
from yoto_lib.icons import set_macos_file_icon
from yoto_lib.mka import set_attachment, wrap_in_mka
from yoto_lib.playlist import write_jsonl

ICON_BASE_URL = "https://media-secure-v2.api.yotoplay.com/icons"
ICON_CACHE_DIR = Path.home() / ".cache" / "yoto" / "icons"


@dataclass
class PullResult:
    card_id: str | None = None
    tracks_downloaded: int = 0
    icons_downloaded: int = 0
    cover_downloaded: bool = False
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


def _download_file(url: str) -> bytes:
    response = httpx.get(url, follow_redirects=True, timeout=300.0)
    response.raise_for_status()
    return response.content


def _extract_icon_hash(icon_ref: str) -> str | None:
    """Extract icon hash from either 'yoto:#hash' or a full URL."""
    if not icon_ref:
        return None
    if icon_ref.startswith("yoto:#"):
        return icon_ref[6:]
    return icon_ref.rstrip("/").rsplit("/", 1)[-1] or None


def _get_icon(icon_ref: str, cache_dir: Path) -> bytes | None:
    """Download an icon, using cache_dir as a file cache."""
    icon_hash = _extract_icon_hash(icon_ref)
    if not icon_hash:
        return None

    cached = cache_dir / f"{icon_hash}.png"
    if cached.exists():
        return cached.read_bytes()

    if icon_ref.startswith("http"):
        url = icon_ref
    else:
        url = f"{ICON_BASE_URL}/{icon_hash}"

    try:
        data = _download_file(url)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data
    except Exception:
        return None


def _apply_icon(mka_path: Path, icon_data: bytes) -> None:
    """Attach icon PNG to MKA and set macOS Finder icon."""
    icon_tmp = mka_path.parent / f".icon_tmp_{mka_path.stem}.png"
    try:
        icon_tmp.write_bytes(icon_data)
        set_attachment(mka_path, icon_tmp, name="icon", mime_type="image/png")
    finally:
        icon_tmp.unlink(missing_ok=True)

    try:
        img = Image.open(io.BytesIO(icon_data))
        set_macos_file_icon(mka_path, img)
    except Exception:
        pass


def _sanitize_filename(name: str) -> str:
    """Remove only characters that are illegal in filenames (/ : \\0)."""
    return name.replace("/", "-").replace(":", "-").replace("\0", "").strip()


@dataclass
class _TrackJob:
    """All info needed to download and process one track."""
    title: str
    filename: str
    track_url: str
    icon_ref: str


def _process_track(job: _TrackJob, folder: Path, cache_dir: Path) -> tuple[bool, bool, str | None]:
    """Download, wrap in MKA, and apply icon for one track.

    Returns (track_ok, icon_ok, error_message).
    """
    safe_name = _sanitize_filename(job.title)
    mka_path = folder / job.filename
    raw_path = folder / f".{safe_name}.raw"
    error = None
    track_ok = False
    icon_ok = False

    try:
        audio_data = _download_file(job.track_url)
        raw_path.write_bytes(audio_data)
        wrap_in_mka(raw_path, mka_path)
        raw_path.unlink(missing_ok=True)
        track_ok = True
    except Exception as exc:
        raw_path.unlink(missing_ok=True)
        return False, False, f"Failed to download {job.title}: {exc}"

    if job.icon_ref:
        try:
            icon_data = _get_icon(job.icon_ref, cache_dir)
            if icon_data:
                _apply_icon(mka_path, icon_data)
                icon_ok = True
        except Exception as exc:
            error = f"Failed to set icon for {job.title}: {exc}"

    return track_ok, icon_ok, error


def pull_playlist(
    folder: Path,
    card_id: str | None = None,
    dry_run: bool = False,
    on_track_done: Optional[Callable[[str], None]] = None,
) -> PullResult:
    """Download a remote Yoto playlist into a local folder."""
    folder = Path(folder)
    result = PullResult(dry_run=dry_run)

    # Determine card ID
    card_id_path = folder / ".yoto-card-id"
    if card_id is None and card_id_path.exists():
        card_id = card_id_path.read_text(encoding="utf-8").strip()

    if card_id is None:
        result.errors.append("No card ID provided and no .yoto-card-id file found.")
        return result

    result.card_id = card_id

    api = YotoAPI()
    remote = api.get_content(card_id, playable=True)

    if dry_run:
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
            (folder / "cover.png").write_bytes(_download_file(cover_url))
            result.cover_downloaded = True
        except Exception as exc:
            result.errors.append(f"Failed to download cover: {exc}")

    # Build track jobs (preserving chapter order)
    chapters = remote.get("content", {}).get("chapters", [])
    jobs: list[_TrackJob] = []
    for chapter in chapters:
        icon_ref = chapter.get("display", {}).get("icon16x16", "")
        for track in chapter.get("tracks", []):
            track_url = track.get("trackUrl", "")
            if not track_url.startswith("http"):
                continue
            title = track.get("title") or chapter.get("title") or chapter.get("key") or "track"
            safe_name = _sanitize_filename(title)
            jobs.append(_TrackJob(
                title=title,
                filename=f"{safe_name}.mka",
                track_url=track_url,
                icon_ref=icon_ref,
            ))

    # Process tracks in parallel
    cache_dir = ICON_CACHE_DIR
    future_to_job = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        for job in jobs:
            future = executor.submit(_process_track, job, folder, cache_dir)
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
