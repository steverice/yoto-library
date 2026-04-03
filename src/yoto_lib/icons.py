"""Icon resolution for Yoto playlist tracks."""

from __future__ import annotations

import io
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from yoto_lib import mka

if TYPE_CHECKING:
    from yoto_lib.api import YotoAPI
    from yoto_lib.playlist import Playlist

# ── Constants ─────────────────────────────────────────────────────────────────

ICON_SIZE = 16

ICNS_SIZES = [16, 32, 64, 128, 256, 512]

ICNS_TYPE_MAP = {
    16: b"icp4",
    32: b"icp5",
    64: b"icp6",
    128: b"ic07",
    256: b"ic08",
    512: b"ic09",
}


# ── Image helpers ─────────────────────────────────────────────────────────────


def nearest_neighbor_upscale(img: Image.Image, target_size: int) -> Image.Image:
    """Resize img to target_size x target_size using nearest-neighbor to preserve crisp pixel grid."""
    return img.resize((target_size, target_size), Image.NEAREST)


def generate_icns_sizes(icon_16: Image.Image) -> dict[int, Image.Image]:
    """Generate all ICNS sizes from a 16x16 source image using nearest-neighbor upscaling."""
    return {size: nearest_neighbor_upscale(icon_16, size) for size in ICNS_SIZES}


# ── ICNS builder ──────────────────────────────────────────────────────────────


def build_icns(icon_16: Image.Image) -> bytes:
    """Build an ICNS file from a 16x16 icon image.

    Each size is PNG-encoded and packed with a 4-byte type tag and 4-byte length
    (covering the type, length, and data).  The file starts with the b"icns"
    magic and a 4-byte total file length.
    """
    sized = generate_icns_sizes(icon_16)

    chunks: list[bytes] = []
    for size in ICNS_SIZES:
        type_tag = ICNS_TYPE_MAP[size]
        img = sized[size]

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_data = buf.getvalue()

        # Entry length = 4 (type) + 4 (length field) + len(data)
        entry_length = 8 + len(png_data)
        chunks.append(type_tag + struct.pack(">I", entry_length) + png_data)

    body = b"".join(chunks)
    total_length = 8 + len(body)  # 4 (magic) + 4 (total length field) + body
    return b"icns" + struct.pack(">I", total_length) + body


# ── macOS icon setter ─────────────────────────────────────────────────────────

_OSASCRIPT_TEMPLATE = """\
use framework "AppKit"
use scripting additions
set ws to current application's NSWorkspace's sharedWorkspace()
set img to current application's NSImage's alloc()'s initWithContentsOfFile:"{icns_path}"
ws's setIcon:img forFile:"{file_path}" options:0
"""


def set_macos_file_icon(file_path: Path, icon_16: Image.Image) -> None:
    """Set the macOS Finder icon for file_path using an ICNS built from icon_16."""
    icns_data = build_icns(icon_16)

    with tempfile.NamedTemporaryFile(suffix=".icns", delete=False) as tmp:
        tmp.write(icns_data)
        icns_path = tmp.name

    try:
        # Paths must be absolute and have quotes escaped for AppleScript
        abs_file = str(Path(file_path).resolve()).replace('"', '\\"')
        script = _OSASCRIPT_TEMPLATE.format(
            icns_path=icns_path,
            file_path=abs_file,
        )
        subprocess.run(
            ["osascript", "-l", "AppleScript", "-e", script],
            capture_output=True,
            check=True,
        )
    finally:
        Path(icns_path).unlink(missing_ok=True)


# ── Public icon matching ──────────────────────────────────────────────────────


def match_public_icon(
    track_title: str,
    public_icons: list[dict],
) -> str | None:
    """Match track_title against Yoto public icon library.

    Compares word overlap and substring matching.  Returns the mediaId of the
    best match, or None if the best score is below 0.5.
    """
    if not public_icons or not track_title:
        return None

    title_lower = track_title.lower()
    title_words = set(title_lower.split())

    best_score = 0.0
    best_id: str | None = None

    for icon in public_icons:
        name: str = icon.get("name", "") or ""
        media_id: str = icon.get("mediaId", "") or ""
        if not media_id:
            continue

        name_lower = name.lower()
        name_words = set(name_lower.split())

        # Word overlap score: Jaccard-like — overlap / union
        if title_words or name_words:
            overlap = len(title_words & name_words)
            union = len(title_words | name_words)
            word_score = overlap / union if union else 0.0
        else:
            word_score = 0.0

        # Substring score
        if title_lower and name_lower:
            if name_lower in title_lower or title_lower in name_lower:
                substring_score = 1.0
            else:
                substring_score = 0.0
        else:
            substring_score = 0.0

        score = max(word_score, substring_score)

        if score > best_score:
            best_score = score
            best_id = media_id

    return best_id if best_score >= 0.5 else None


# ── resolve_icons ─────────────────────────────────────────────────────────────


def resolve_icons(playlist: "Playlist", api: "YotoAPI") -> dict[str, str]:
    """Resolve icon IDs for each track in the playlist.

    Resolution order for each track file:
    1. MKA attachment named "icon" → upload via api.upload_icon, set macOS icon
    2. Match from the Yoto public icon library (lazy-loaded)
    3. AI generation — TODO: depends on grid validation (Task 5)

    Returns dict mapping filename → mediaId.
    """
    result: dict[str, str] = {}
    public_icons: list[dict] | None = None  # lazy-loaded

    for filename in playlist.track_files:
        track_path = playlist.path / filename
        media_id: str | None = None

        # 1. MKA attachment
        try:
            attachment_bytes = mka.get_attachment(track_path, "icon")
        except Exception:
            attachment_bytes = None

        if attachment_bytes is not None:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(attachment_bytes)
                tmp_path = Path(tmp.name)
            try:
                upload_result = api.upload_icon(tmp_path)
                media_id = upload_result.get("mediaId") or upload_result.get("id")
                if media_id:
                    try:
                        icon_img = Image.open(io.BytesIO(attachment_bytes))
                        icon_16 = nearest_neighbor_upscale(icon_img, ICON_SIZE)
                        set_macos_file_icon(track_path, icon_16)
                    except Exception:
                        pass  # macOS icon setting is best-effort
            finally:
                tmp_path.unlink(missing_ok=True)

        # 2. Public icon match
        if media_id is None:
            if public_icons is None:
                try:
                    public_icons = api.get_public_icons()
                except Exception:
                    public_icons = []

            # Derive title from filename for matching
            track_title = Path(filename).stem
            try:
                tags = mka.read_tags(track_path)
                track_title = tags.get("title") or track_title
            except Exception:
                pass

            media_id = match_public_icon(track_title, public_icons)

        # 3. AI generation — TODO: depends on grid validation (Task 5)

        if media_id is not None:
            result[filename] = media_id

    return result
