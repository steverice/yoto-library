"""MKA container handling: wrap audio, read/write tags, manage attachments."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
    "lyrics": "LYRICS",
    "lyrics_summary": "YOTO_LYRICS_SUMMARY",
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


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
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
    _run(["ffmpeg", "-y", "-i", str(source), "-map", "0", "-c", "copy", str(output)])


# Codec → (file extension, ffmpeg muxer format)
_CODEC_CONTAINERS: dict[str, tuple[str, str]] = {
    "aac": (".m4a", "ipod"),
    "mp3": (".mp3", "mp3"),
    "opus": (".ogg", "ogg"),
    "vorbis": (".ogg", "ogg"),
    "flac": (".flac", "flac"),
    "alac": (".m4a", "ipod"),
    "pcm_s16le": (".wav", "wav"),
    "pcm_s24le": (".wav", "wav"),
}

# Source format extension → (file extension, ffmpeg muxer format)
_FORMAT_CONTAINERS: dict[str, tuple[str, str]] = {
    "m4a": (".m4a", "ipod"),
    "mp3": (".mp3", "mp3"),
    "ogg": (".ogg", "ogg"),
    "opus": (".ogg", "ogg"),
    "flac": (".flac", "flac"),
    "wav": (".wav", "wav"),
    "aac": (".m4a", "ipod"),
}


def extract_audio(mka_path: Path, output_dir: Path) -> Path:
    """Extract audio from MKA into its native container (lossless remux).

    Uses YOTO_SOURCE_FORMAT tag if available, falls back to codec probe.
    Returns the path to the extracted file.
    """
    # Try source format tag first
    tags = read_tags(mka_path)
    source_fmt = tags.get("source_format", "").lower()
    if source_fmt and source_fmt in _FORMAT_CONTAINERS:
        ext, fmt = _FORMAT_CONTAINERS[source_fmt]
        logger.debug("extract_audio: %s -> %s (from YOTO_SOURCE_FORMAT=%s)", mka_path.name, ext, source_fmt)
    else:
        # Fall back to codec probe
        info = probe_audio(mka_path)
        audio_streams = [s for s in info["streams"] if s.get("codec_type") == "audio"]
        codec = audio_streams[0]["codec_name"] if audio_streams else "unknown"
        if codec in _CODEC_CONTAINERS:
            ext, fmt = _CODEC_CONTAINERS[codec]
            logger.debug("extract_audio: %s -> %s (from codec=%s)", mka_path.name, ext, codec)
        else:
            # Unknown codec — transcode to MP3 as safe fallback
            logger.warning("extract_audio: unknown codec %s in %s, transcoding to MP3", codec, mka_path.name)
            output = output_dir / (mka_path.stem + ".mp3")
            _run(["ffmpeg", "-y", "-i", str(mka_path), "-map", "0:a",
                  "-c:a", "libmp3lame", "-b:a", "192k",
                  "-map_metadata", "-1", "-fflags", "+bitexact", str(output)])
            return output

    output = output_dir / (mka_path.stem + ext)
    _run(["ffmpeg", "-y", "-i", str(mka_path), "-map", "0:a", "-c", "copy",
          "-map_metadata", "-1", "-fflags", "+bitexact", "-f", fmt, str(output)])
    return output


def probe_audio(path: Path) -> dict[str, Any]:
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


_MIN_ALBUM_ART_SIZE = 100  # pixels; skip track icons (16x16) and tiny images


def extract_album_art(mka_path: Path) -> bytes | None:
    """Extract embedded album art (video stream) from an MKA file.

    Returns PNG image bytes if a suitable video stream exists, None otherwise.
    Skips small images (e.g. 16x16 track icons) that aren't album art.
    """
    info = probe_audio(mka_path)
    # Find the first video stream large enough to be album art
    video_idx = 0
    art_stream = None
    for s in info["streams"]:
        if s.get("codec_type") != "video":
            continue
        w = int(s.get("width", 0))
        h = int(s.get("height", 0))
        if w >= _MIN_ALBUM_ART_SIZE and h >= _MIN_ALBUM_ART_SIZE:
            art_stream = video_idx
            break
        video_idx += 1

    if art_stream is None:
        return None

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        out_path = Path(f.name)

    try:
        result = _run(
            ["ffmpeg", "-y", "-i", str(mka_path),
             "-map", f"0:v:{art_stream}", "-frames:v", "1",
             str(out_path)],
            check=False,
        )
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            return None
        return out_path.read_bytes()
    finally:
        out_path.unlink(missing_ok=True)


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
    "lyrics": "lyrics",
    "LYRICS": "lyrics",
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


# ── Source patch (bsdiff/bspatch) ────────────────────────────────────────────

import shutil

PATCH_ATTACHMENT_NAME = "source.patch"


def generate_source_patch(original_path: Path, mka_path: Path) -> bool:
    """Generate a bsdiff patch between a deterministic reconstruction and the original.

    Stores the patch as an MKA attachment. Returns True if patch was stored,
    False if bsdiff is not available or generation failed.
    """
    if not shutil.which("bsdiff"):
        logger.warning("bsdiff not found — skipping source patch generation (export will not be byte-perfect)")
        return False

    with tempfile.TemporaryDirectory(prefix="yoto-patch-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        try:
            # Deterministic reconstruction from MKA
            reconstructed = extract_audio(mka_path, tmpdir_path)

            # Generate patch: bsdiff <reconstructed> <original> <patch>
            patch_path = tmpdir_path / "source.patch"
            _run(["bsdiff", str(reconstructed), str(original_path), str(patch_path)])

            patch_size = patch_path.stat().st_size
            original_size = original_path.stat().st_size
            logger.debug(
                "generate_source_patch: %s -> %d bytes patch (%.1f%% of original)",
                mka_path.name, patch_size, 100 * patch_size / original_size if original_size else 0,
            )

            # Store as attachment
            set_attachment(mka_path, patch_path, name=PATCH_ATTACHMENT_NAME, mime_type="application/octet-stream")
            return True
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning("generate_source_patch failed for %s: %s", mka_path.name, exc)
            return False


def apply_source_patch(extracted_path: Path, mka_path: Path, output_path: Path) -> bool:
    """Apply a stored bsdiff patch to a reconstructed file to recover the original.

    Returns True if patch was applied, False if no patch exists or bspatch failed.
    """
    patch_data = get_attachment(mka_path, PATCH_ATTACHMENT_NAME)
    if patch_data is None:
        return False

    with tempfile.TemporaryDirectory(prefix="yoto-patch-") as tmpdir:
        patch_file = Path(tmpdir) / "source.patch"
        patch_file.write_bytes(patch_data)

        try:
            _run(["bspatch", str(extracted_path), str(output_path), str(patch_file)])
            logger.debug("apply_source_patch: %s -> %s", mka_path.name, output_path.name)
            return True
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning("apply_source_patch failed for %s: %s", mka_path.name, exc)
            return False
