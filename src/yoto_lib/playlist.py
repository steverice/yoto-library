"""Playlist model — bridges filesystem layout and the Yoto API content schema."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────

AUDIO_EXTENSIONS: set[str] = {
    ".mka", ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma"
}


# ── JSONL helpers ─────────────────────────────────────────────────────────────


def read_jsonl(path: Path) -> list[str]:
    """Parse each non-blank line with json.loads; return list of strings."""
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            result.append(json.loads(line))
    return result


def write_jsonl(path: Path, filenames: list[str]) -> None:
    """Write each filename as a JSON string per line."""
    path.write_text(
        "\n".join(json.dumps(name) for name in filenames) + ("\n" if filenames else ""),
        encoding="utf-8",
    )


# ── Scanning ──────────────────────────────────────────────────────────────────


def scan_audio_files(folder: Path) -> list[Path]:
    """Return sorted list of Path objects for audio files in folder."""
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )


# ── Playlist dataclass ────────────────────────────────────────────────────────


@dataclass
class Playlist:
    path: Path
    title: str
    track_files: list[str]
    card_id: Optional[str]
    description: Optional[str]
    has_cover: bool
    missing_files: list[str]

    @property
    def cover_path(self) -> Path:
        return self.path / "cover.png"

    @property
    def description_path(self) -> Path:
        return self.path / "description.txt"

    @property
    def jsonl_path(self) -> Path:
        return self.path / "playlist.jsonl"

    @property
    def card_id_path(self) -> Path:
        return self.path / ".yoto-card-id"


# ── load_playlist ─────────────────────────────────────────────────────────────


def load_playlist(folder: Path) -> Playlist:
    """
    Load a playlist from a folder on disk.

    - Read playlist.jsonl if exists, else auto-generate (alphabetical) and write it.
    - Separate existing vs missing files from the jsonl.
    - Append files on disk not listed in jsonl (sorted).
    - Read .yoto-card-id if exists.
    - Read description.txt if exists.
    - Check if cover.png exists.
    """
    folder = Path(folder)
    jsonl_path = folder / "playlist.jsonl"
    audio_on_disk = {p.name for p in scan_audio_files(folder)}

    if jsonl_path.exists():
        listed = read_jsonl(jsonl_path)
    else:
        # Auto-generate from disk, alphabetical
        listed = sorted(audio_on_disk)
        write_jsonl(jsonl_path, listed)

    # Partition listed into present and missing
    existing = [f for f in listed if f in audio_on_disk]
    missing = [f for f in listed if f not in audio_on_disk]

    # Append files on disk not yet listed, sorted
    listed_set = set(listed)
    unlisted = sorted(f for f in audio_on_disk if f not in listed_set)
    track_files = existing + unlisted

    # .yoto-card-id
    card_id_path = folder / ".yoto-card-id"
    card_id: Optional[str] = None
    if card_id_path.exists():
        card_id = card_id_path.read_text(encoding="utf-8").strip()

    # description.txt
    description_path = folder / "description.txt"
    description: Optional[str] = None
    if description_path.exists():
        description = description_path.read_text(encoding="utf-8")

    # cover.png
    has_cover = (folder / "cover.png").exists()

    return Playlist(
        path=folder,
        title=folder.name,
        track_files=track_files,
        card_id=card_id,
        description=description,
        has_cover=has_cover,
        missing_files=missing,
    )


# ── build_content_schema ──────────────────────────────────────────────────────


def _title_from_filename(filename: str) -> str:
    """Derive a human-readable title from a filename (strip extension)."""
    return Path(filename).stem


def build_content_schema(
    playlist: Playlist,
    track_hashes: dict[str, str],
    icon_ids: dict[str, str],
    cover_url: Optional[str],
) -> dict:
    """
    Build Yoto API content JSON from a Playlist.

    - One chapter per track (key=filename, title derived from filename).
    - trackUrl = yoto:#<sha256>
    - Include icon display if icon_id present.
    - Include cardId if playlist has one.
    - Include cover URL in metadata.
    """
    chapters: list = []
    for idx, filename in enumerate(playlist.track_files):
        sha256 = track_hashes.get(filename, "")
        chapter_key = f"ch{idx:03d}"
        track_key = f"t{idx:03d}"
        chapter: dict = {
            "key": chapter_key,
            "title": _title_from_filename(filename),
            "tracks": [
                {"key": track_key, "title": _title_from_filename(filename), "type": "audio", "trackUrl": f"yoto:#{sha256}"}
            ],
        }
        if filename in icon_ids:
            media_id = icon_ids[filename]
            if not media_id.startswith("yoto:#"):
                media_id = f"yoto:#{media_id}"
            display = {"icon16x16": media_id}
            chapter["display"] = display
            chapter["tracks"][0]["display"] = display
        chapters.append(chapter)

    content: dict = {"chapters": chapters}

    schema: dict = {"title": playlist.title, "content": content}

    metadata: dict = {}
    if cover_url:
        metadata["cover"] = {"imageL": cover_url}
    if playlist.description:
        metadata["description"] = playlist.description
    if metadata:
        schema["metadata"] = metadata

    if playlist.card_id:
        schema["cardId"] = playlist.card_id

    return schema


# ── PlaylistDiff dataclass ────────────────────────────────────────────────────


@dataclass
class PlaylistDiff:
    new_tracks: list[str]
    removed_tracks: list[str]
    order_changed: bool
    cover_changed: bool
    metadata_changed: bool
    icon_changes: list[str] = field(default_factory=list)


# ── diff_playlists ────────────────────────────────────────────────────────────


def diff_playlists(playlist: Playlist, remote: Optional[dict]) -> PlaylistDiff:
    """
    Compare local playlist against remote state dict.

    remote dict shape (when not None):
      {
        "tracks": list[str],          # ordered track titles
        "description": str | None,    # optional
        "has_cover": bool,            # optional
      }

    If remote is None, everything is considered new/changed.
    """
    if remote is None:
        return PlaylistDiff(
            new_tracks=list(playlist.track_files),
            removed_tracks=[],
            order_changed=False,
            cover_changed=True,
            metadata_changed=True,
        )

    remote_tracks: list[str] = remote.get("tracks", [])

    # Compare by title: local filenames map to titles via stem
    local_titles = [_title_from_filename(f) for f in playlist.track_files]
    local_set = set(local_titles)
    remote_set = set(remote_tracks)

    # Report new/removed using filenames for local, titles for remote
    title_to_file = {_title_from_filename(f): f for f in playlist.track_files}
    new_tracks = [f for f in playlist.track_files if _title_from_filename(f) not in remote_set]
    removed_tracks = [t for t in remote_tracks if t not in local_set]

    # Order changed if same tracks but different order
    common_local = [t for t in local_titles if t in remote_set]
    common_remote = [t for t in remote_tracks if t in local_set]
    order_changed = common_local != common_remote

    # Cover changed
    remote_has_cover: bool = remote.get("has_cover", False)
    cover_changed = playlist.has_cover != remote_has_cover

    # Metadata changed — compare description
    remote_description = remote.get("description", None)
    metadata_changed = playlist.description != remote_description

    return PlaylistDiff(
        new_tracks=new_tracks,
        removed_tracks=removed_tracks,
        order_changed=order_changed,
        cover_changed=cover_changed,
        metadata_changed=metadata_changed,
    )
