"""Icon resolution for Yoto playlist tracks."""

from __future__ import annotations

import io
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from PIL import Image

from yoto_lib import mka

if TYPE_CHECKING:
    from yoto_lib.api import YotoAPI
    from yoto_lib.playlist import Playlist

# ── Constants ─────────────────────────────────────────────────────────────────

ICON_SIZE = 16
ICON_BASE_URL = "https://media-secure-v2.api.yotoplay.com/icons"
ICON_CACHE_DIR = Path.home() / ".cache" / "yoto" / "icons"

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


# ── Icon download / cache ────────────────────────────────────────────────────


def _download_bytes(url: str) -> bytes:
    """Fetch raw bytes from a URL."""
    response = httpx.get(url, follow_redirects=True, timeout=300.0)
    response.raise_for_status()
    return response.content


def extract_icon_hash(icon_ref: str) -> str | None:
    """Extract icon hash from either 'yoto:#hash' or a full URL."""
    if not icon_ref:
        return None
    if icon_ref.startswith("yoto:#"):
        return icon_ref[6:]
    return icon_ref.rstrip("/").rsplit("/", 1)[-1] or None


def download_icon(icon_ref: str, cache_dir: Path = ICON_CACHE_DIR) -> bytes | None:
    """Download an icon by ref (yoto:#hash, URL, or bare mediaId), using file cache."""
    icon_hash = extract_icon_hash(icon_ref) if ":" in icon_ref or "/" in icon_ref else icon_ref
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
        data = _download_bytes(url)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data
    except Exception:
        return None


def apply_icon_to_mka(mka_path: Path, icon_data: bytes) -> None:
    """Attach icon PNG to MKA and set macOS Finder icon."""
    icon_tmp = mka_path.parent / f".icon_tmp_{mka_path.stem}.png"
    try:
        icon_tmp.write_bytes(icon_data)
        mka.set_attachment(mka_path, icon_tmp, name="icon", mime_type="image/png")
    finally:
        icon_tmp.unlink(missing_ok=True)

    try:
        img = Image.open(io.BytesIO(icon_data))
        set_macos_file_icon(mka_path, img)
    except Exception:
        pass


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


# ── AI icon generation ────────────────────────────────────────────────────────

GRID_SIZE = 8
TILE_SIZE = 128  # 1024 / 8
CANVAS_SIZE = 1024


def build_icon_prompt(track_title: str) -> str:
    """Build a prompt for generating an 8x8 grid of identical 16x16-style icons."""
    return (
        f"Generate an 8x8 grid of identical icons on a 1024x1024 pixel canvas. "
        f"Each icon is 128x128 pixels. Every cell in the grid shows the exact same icon. "
        f"The icon depicts: {track_title}. "
        f"Style: bold simple shapes, flat solid colors, minimal detail, high contrast. "
        f"Suitable for a 16x16 pixel icon when downscaled. "
        f"Do not include any text, letters, numbers, or lettering."
    )


def generate_track_icon(track_title: str) -> bytes | None:
    """Generate a 16x16 icon via the AI grid technique. Returns PNG bytes or None."""
    try:
        from yoto_lib.image_providers import get_provider
        provider = get_provider()
    except Exception:
        return None

    prompt = build_icon_prompt(track_title)

    try:
        image_bytes = provider.generate(prompt, CANVAS_SIZE, CANVAS_SIZE)
    except Exception:
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Crop center tile from the 8x8 grid
        center = GRID_SIZE // 2
        left = center * TILE_SIZE
        top = center * TILE_SIZE
        tile = img.crop((left, top, left + TILE_SIZE, top + TILE_SIZE))
        # Downscale to 16x16
        icon_16 = tile.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
        buf = io.BytesIO()
        icon_16.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


# ── resolve_icons ─────────────────────────────────────────────────────────────


def _derive_track_title(track_path: Path, filename: str) -> str:
    """Get a human-readable title for matching: MKA tag → filename stem."""
    title = Path(filename).stem
    try:
        tags = mka.read_tags(track_path)
        title = tags.get("title") or title
    except Exception:
        pass
    return title


def _upload_icon_bytes(api: "YotoAPI", icon_bytes: bytes) -> str | None:
    """Upload icon bytes to Yoto API, return mediaId or None on failure."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(icon_bytes)
        tmp_path = Path(tmp.name)
    try:
        upload_result = api.upload_icon(tmp_path, auto_convert=True)
        return upload_result.get("mediaId") or upload_result.get("id")
    except Exception:
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def resolve_icons(playlist: "Playlist", api: "YotoAPI") -> dict[str, str]:
    """Resolve and embed icons for each track in the playlist.

    For each track, ensures an icon is written to the MKA file on disk,
    then returns the mediaId for the content schema.

    Resolution order:
    1. MKA attachment named "icon" → already on disk, upload to API
    2. Match from Yoto public icon library → download, write to MKA
    3. AI generation via grid technique → generate, write to MKA

    Returns dict mapping filename → mediaId.
    """
    result: dict[str, str] = {}
    public_icons: list[dict] | None = None  # lazy-loaded

    for filename in playlist.track_files:
        track_path = playlist.path / filename
        media_id: str | None = None
        icon_bytes: bytes | None = None

        # 1. Check for existing MKA icon attachment
        try:
            icon_bytes = mka.get_attachment(track_path, "icon")
        except Exception:
            icon_bytes = None

        if icon_bytes is not None:
            media_id = _upload_icon_bytes(api, icon_bytes)
        else:
            track_title = _derive_track_title(track_path, filename)

            # 2. Public icon match
            if public_icons is None:
                try:
                    public_icons = api.get_public_icons()
                except Exception:
                    public_icons = []

            matched_id = match_public_icon(track_title, public_icons)
            if matched_id:
                dl_bytes = download_icon(matched_id)
                if dl_bytes:
                    apply_icon_to_mka(track_path, dl_bytes)
                    icon_bytes = dl_bytes
                media_id = matched_id  # use Yoto's public ID directly

            # 3. AI generation
            if media_id is None:
                icon_bytes = generate_track_icon(track_title)
                if icon_bytes:
                    apply_icon_to_mka(track_path, icon_bytes)
                    media_id = _upload_icon_bytes(api, icon_bytes)

        # Set macOS Finder icon (best-effort, skip if already set by apply_icon_to_mka)
        if icon_bytes is not None and media_id is not None:
            # apply_icon_to_mka already sets Finder icon for steps 2 & 3;
            # for step 1 (pre-existing attachment) set it here
            try:
                img = Image.open(io.BytesIO(icon_bytes))
                set_macos_file_icon(track_path, img)
            except Exception:
                pass

        if media_id is not None:
            result[filename] = media_id

    return result
