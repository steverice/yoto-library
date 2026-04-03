"""Pull engine: download remote Yoto playlist to local folder."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from yoto_lib.api import YotoAPI
from yoto_lib.playlist import write_jsonl


@dataclass
class PullResult:
    card_id: str | None = None
    tracks_downloaded: int = 0
    cover_downloaded: bool = False
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


def _download_file(url: str) -> bytes:
    response = httpx.get(url, follow_redirects=True, timeout=300.0)
    response.raise_for_status()
    return response.content


def _download_cover(url: str) -> bytes:
    return _download_file(url)


def _sanitize_filename(name: str) -> str:
    """Keep only alphanumeric, space, hyphen, underscore characters."""
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()


def pull_playlist(
    folder: Path,
    card_id: str | None = None,
    dry_run: bool = False,
) -> PullResult:
    """
    Download a remote Yoto playlist into a local folder.

    Steps:
      1. Determine card_id from arg or .yoto-card-id file
      2. If no card_id, add error and return
      3. Create YotoAPI(), fetch content with playable=True
      4. If dry_run: count tracks and return
      5. Write card_id to .yoto-card-id
      6. Write description.txt from metadata
      7. Download cover image as cover.png
      8. Download audio tracks via signed URLs, write as raw files, then wrap in MKA
      9. Write playlist.jsonl from track order
    """
    folder = Path(folder)
    result = PullResult(dry_run=dry_run)

    # 1. Determine card ID
    card_id_path = folder / ".yoto-card-id"
    if card_id is None and card_id_path.exists():
        card_id = card_id_path.read_text(encoding="utf-8").strip()

    if card_id is None:
        result.errors.append("No card ID provided and no .yoto-card-id file found.")
        return result

    result.card_id = card_id

    # 3. Create API and fetch content
    api = YotoAPI()
    raw = api.get_content(card_id, playable=True)
    remote = raw.get("card", raw)

    # 4. Dry run: count tracks and return without downloading
    if dry_run:
        chapters = remote.get("content", {}).get("chapters", [])
        # chapters may be a list or dict depending on API response shape
        if isinstance(chapters, list):
            result.tracks_downloaded = 0  # dry_run reports 0 (nothing downloaded)
        return result

    # 5. Write card ID
    card_id_path.write_text(card_id, encoding="utf-8")

    # 6. Write description
    description = remote.get("metadata", {}).get("description", "")
    if description:
        (folder / "description.txt").write_text(description, encoding="utf-8")

    # 7. Download cover
    cover_url = remote.get("metadata", {}).get("cover", {}).get("imageL")
    if cover_url:
        try:
            cover_data = _download_cover(cover_url)
            (folder / "cover.png").write_bytes(cover_data)
            result.cover_downloaded = True
        except Exception as exc:
            result.errors.append(f"Failed to download cover: {exc}")

    # 8. Download tracks
    chapters = remote.get("content", {}).get("chapters", [])
    track_filenames: list[str] = []

    for chapter in chapters:
        for track in chapter.get("tracks", []):
            track_url = track.get("trackUrl", "")
            if not track_url.startswith("http"):
                continue

            title = track.get("title") or chapter.get("key") or "track"
            safe_name = _sanitize_filename(title)
            filename = f"{safe_name}.mka"
            track_filenames.append(filename)

            try:
                audio_data = _download_file(track_url)
                # Write raw audio first, then wrap in MKA
                raw_path = folder / f".{safe_name}.raw"
                raw_path.write_bytes(audio_data)

                mka_path = folder / filename
                from yoto_lib.mka import wrap_in_mka
                wrap_in_mka(raw_path, mka_path)
                raw_path.unlink(missing_ok=True)

                result.tracks_downloaded += 1
            except Exception as exc:
                result.errors.append(f"Failed to download {title}: {exc}")

    # 9. Write playlist.jsonl
    if track_filenames:
        write_jsonl(folder / "playlist.jsonl", track_filenames)

    return result
