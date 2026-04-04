"""MKA container handling: wrap audio, read/write tags, manage attachments."""

from __future__ import annotations

import json
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Maps our internal field names to Matroska tag names.
# Standard Matroska tags are uppercase. Custom Yoto fields use YOTO_ prefix.
TAG_MAP = {
    "title": "TITLE",
    "artist": "ARTIST",
    "language": "LANGUAGE",
    "copyright": "COPYRIGHT",
    "description": "COMMENT",
    "author": "ARTIST",
    "read_by": "YOTO_READ_BY",
    "category": "YOTO_CATEGORY",
    "min_age": "YOTO_MIN_AGE",
    "max_age": "YOTO_MAX_AGE",
    "genre": "GENRE",
    "composer": "COMPOSER",
    "album_artist": "ALBUM_ARTIST",
    "album": "ALBUM",
    "date": "DATE_RELEASED",
    "track": "PART_NUMBER",
    "disc": "DISC_NUMBER",
}

# Reverse map for reading tags back (first occurrence wins, so "artist" beats "author")
_REVERSE_TAG_MAP = {}
for _k, _v in TAG_MAP.items():
    if _v not in _REVERSE_TAG_MAP:
        _REVERSE_TAG_MAP[_v] = _k

# ffprobe normalises some Matroska tag names when reading back; add aliases so
# read_tags() can resolve them via the same .upper() lookup path.
# PART_NUMBER is exposed by ffprobe as the conventional "track" key.
_REVERSE_TAG_MAP["TRACK"] = "track"


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


def wrap_in_mka(source: Path, output: Path) -> None:
    """Wrap any audio file in an MKA container without re-encoding."""
    if not Path(source).exists():
        raise FileNotFoundError(f"Source file not found: {source}")
    _run(["ffmpeg", "-y", "-i", str(source), "-c", "copy", str(output)])


def probe_audio(path: Path) -> dict:
    """Get audio file info via ffprobe."""
    result = _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ])
    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    # ffprobe may return comma-separated format names (e.g. "matroska,webm");
    # return the primary (first) name only.
    raw_format = fmt.get("format_name", "")
    primary_format = raw_format.split(",")[0] if raw_format else ""
    return {
        "format": primary_format,
        "duration": float(fmt.get("duration", 0)),
        "size": int(fmt.get("size", 0)),
        "streams": data.get("streams", []),
    }


def write_tags(mka_path: Path, tags: dict[str, str]) -> None:
    """Write Matroska tags to an MKA file using mkvpropedit."""
    # Build an XML tags file for mkvpropedit
    root = ET.Element("Tags")
    tag_el = ET.SubElement(root, "Tag")
    targets = ET.SubElement(tag_el, "Targets")
    ET.SubElement(targets, "TargetTypeValue").text = "50"  # Album level

    for field, value in tags.items():
        mkv_name = TAG_MAP.get(field, f"YOTO_{field.upper()}")
        simple = ET.SubElement(tag_el, "Simple")
        ET.SubElement(simple, "Name").text = mkv_name
        ET.SubElement(simple, "String").text = value

    with tempfile.NamedTemporaryFile(suffix=".xml", mode="w", delete=False) as f:
        tree = ET.ElementTree(root)
        tree.write(f, xml_declaration=True, encoding="unicode")
        tags_file = f.name

    try:
        _run(["mkvpropedit", str(mka_path), "--tags", f"global:{tags_file}"])
    finally:
        Path(tags_file).unlink(missing_ok=True)


def read_tags(mka_path: Path) -> dict[str, str]:
    """Read Matroska tags from an MKA file using ffprobe."""
    result = _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(mka_path),
    ])
    fmt_data = json.loads(result.stdout)
    raw_tags = fmt_data.get("format", {}).get("tags", {})

    tags = {}
    for raw_name, value in raw_tags.items():
        field = _REVERSE_TAG_MAP.get(raw_name.upper())
        if field:
            tags[field] = value
        elif raw_name.upper().startswith("YOTO_"):
            field = raw_name.upper().removeprefix("YOTO_").lower()
            tags[field] = value

    return tags


# Maps common source-format tag names (as reported by ffprobe) to internal field names.
# ffprobe normalises tag keys to lowercase for most formats.
_SOURCE_TAG_ALIASES: dict[str, str] = {
    "title": "title",
    "artist": "artist",
    "album_artist": "album_artist",
    "album": "album",
    "genre": "genre",
    "composer": "composer",
    "date": "date",
    "track": "track",
    "disc": "disc",
    "language": "language",
    "copyright": "copyright",
    "comment": "description",
    # MKA/Matroska names (uppercase in ffprobe output for matroska)
    "TITLE": "title",
    "ARTIST": "artist",
    "ALBUM_ARTIST": "album_artist",
    "ALBUM": "album",
    "GENRE": "genre",
    "COMPOSER": "composer",
    "DATE_RELEASED": "date",
    "PART_NUMBER": "track",
    "DISC_NUMBER": "disc",
    "LANGUAGE": "language",
    "COPYRIGHT": "copyright",
    "COMMENT": "description",
}


def read_source_tags(path: Path) -> dict[str, str]:
    """Read metadata tags from any audio file via ffprobe.

    Works with mp3, m4a, flac, wav, ogg, mka, etc. Returns a dict
    using internal field names (title, artist, genre, etc.).
    """
    result = _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(path),
    ])
    fmt_data = json.loads(result.stdout)
    raw_tags = fmt_data.get("format", {}).get("tags", {})

    tags: dict[str, str] = {}
    for raw_name, value in raw_tags.items():
        field = _SOURCE_TAG_ALIASES.get(raw_name)
        if field and field not in tags:  # first occurrence wins
            tags[field] = value
    return tags


def set_attachment(
    mka_path: Path,
    file_path: Path,
    name: str,
    mime_type: str,
) -> None:
    """Add or replace an attachment in an MKA file."""
    # First try to remove existing attachment with same name
    remove_attachment(mka_path, name)

    # Name and mime-type flags must precede --add-attachment
    _run([
        "mkvpropedit", str(mka_path),
        "--attachment-name", name,
        "--attachment-mime-type", mime_type,
        "--add-attachment", str(file_path),
    ])


def get_attachment(mka_path: Path, name: str) -> bytes | None:
    """Extract an attachment from an MKA file by name."""
    # Get attachment info via mkvmerge -J
    result = _run(["mkvmerge", "-J", str(mka_path)])
    data = json.loads(result.stdout)

    attachments = data.get("attachments", [])
    target = None
    for att in attachments:
        if att.get("file_name") == name:
            target = att
            break

    if target is None:
        return None

    att_id = target["id"]
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        out_path = f.name

    try:
        _run([
            "mkvextract", str(mka_path),
            "attachments", f"{att_id}:{out_path}",
        ])
        return Path(out_path).read_bytes()
    finally:
        Path(out_path).unlink(missing_ok=True)


def remove_attachment(mka_path: Path, name: str) -> None:
    """Remove an attachment from an MKA file by name."""
    result = _run(["mkvmerge", "-J", str(mka_path)])
    data = json.loads(result.stdout)

    attachments = data.get("attachments", [])
    for att in attachments:
        if att.get("file_name") == name:
            _run([
                "mkvpropedit", str(mka_path),
                "--delete-attachment", f"name:{name}",
            ], check=False)
            return
