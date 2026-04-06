"""Cover image generation for Yoto playlists."""

from __future__ import annotations

import hashlib
import io
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from yoto_lib.image_providers import get_provider
from yoto_lib import mka

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable
    from yoto_lib.playlist import Playlist

COVER_WIDTH = 638
COVER_HEIGHT = 1011


def resize_cover(source: Path, output: Path) -> None:
    """Open source image, center-crop to 638:1011 aspect ratio, resize, save as PNG."""
    img = Image.open(source)
    src_w, src_h = img.size

    target_ratio = COVER_WIDTH / COVER_HEIGHT  # ~0.631

    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # Image is wider than target: crop left and right
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        crop_box = (left, 0, left + new_w, src_h)
    else:
        # Image is taller than target (or equal): crop top and bottom
        new_h = int(src_w / target_ratio)
        top = (src_h - new_h) // 2
        crop_box = (0, top, src_w, top + new_h)

    img = img.crop(crop_box)
    img = img.resize((COVER_WIDTH, COVER_HEIGHT), Image.LANCZOS)
    img.save(output, format="PNG")


def pad_to_cover(art_bytes: bytes) -> bytes:
    """Scale album art to fit within cover dimensions and pad with edge color.

    Returns PNG bytes of the padded image at COVER_WIDTH x COVER_HEIGHT.
    """
    art = Image.open(io.BytesIO(art_bytes))

    # Scale to fit within cover dimensions (constrained by width or height)
    scale = min(COVER_WIDTH / art.width, COVER_HEIGHT / art.height)
    new_w = int(art.width * scale)
    new_h = int(art.height * scale)
    scaled = art.resize((new_w, new_h), Image.LANCZOS)

    # Sample average edge color from the original image
    edge_pixels = []
    for x in range(art.width):
        edge_pixels.append(art.getpixel((x, 0)))
        edge_pixels.append(art.getpixel((x, art.height - 1)))
    for y in range(art.height):
        edge_pixels.append(art.getpixel((0, y)))
        edge_pixels.append(art.getpixel((art.width - 1, y)))

    avg_r = sum(p[0] for p in edge_pixels) // len(edge_pixels)
    avg_g = sum(p[1] for p in edge_pixels) // len(edge_pixels)
    avg_b = sum(p[2] for p in edge_pixels) // len(edge_pixels)

    cover = Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT), (avg_r, avg_g, avg_b))
    x_offset = (COVER_WIDTH - new_w) // 2
    y_offset = (COVER_HEIGHT - new_h) // 2
    cover.paste(scaled, (x_offset, y_offset))

    buf = io.BytesIO()
    cover.save(buf, format="PNG")
    return buf.getvalue()


def build_cover_prompt(
    description: str | None,
    track_titles: list[str],
    artists: list[str],
    playlist_title: str | None = None,
) -> str:
    """Build a text prompt for image generation from playlist metadata."""
    parts: list[str] = []

    if playlist_title:
        parts.append(f'Playlist: "{playlist_title}".')

    if description:
        parts.append(description.strip())

    titles = track_titles[:10]
    if titles:
        parts.append("Tracks: " + ", ".join(titles))

    unique_artists = list(dict.fromkeys(artists))  # deduplicate, preserve order
    if unique_artists:
        parts.append("Artists: " + ", ".join(unique_artists))

    parts.append(
        "Create a portrait-oriented children's book cover illustration."
        " Display the playlist name as a title inside a decorative banner"
        " in the upper portion of the image. The banner and all text must be"
        " well within the image boundaries — nothing near the edges."
    )

    return " ".join(parts)


def try_shared_album_art(
    playlist: "Playlist",
    log: "Callable[[str], None] | None" = None,
) -> bool:
    """Check if all tracks share identical album art; if so, save it as the cover.

    Extracts embedded album art from each track's video stream, hashes them,
    and if all hashes match, resizes the shared art to cover dimensions.

    Returns True if shared art was used, False otherwise.
    """
    _log = log or (lambda msg: None)
    if not playlist.track_files:
        return False

    first_hash: str | None = None
    first_art_bytes: bytes | None = None

    for filename in playlist.track_files:
        track_path = playlist.path / filename
        if not track_path.exists():
            logger.debug("try_shared_album_art: track not found: %s", filename)
            return False

        art_bytes = mka.extract_album_art(track_path)
        if art_bytes is None:
            logger.debug("try_shared_album_art: no album art in %s", filename)
            return False

        art_hash = hashlib.sha256(art_bytes).hexdigest()

        if first_hash is None:
            first_hash = art_hash
            first_art_bytes = art_bytes
            logger.debug("try_shared_album_art: first track art hash=%s (%s)", art_hash[:12], filename)
        elif art_hash != first_hash:
            logger.debug(
                "try_shared_album_art: art mismatch at %s (hash=%s, expected=%s)",
                filename, art_hash[:12], first_hash[:12],
            )
            return False

    assert first_art_bytes is not None
    logger.info(
        "try_shared_album_art: all %d tracks share identical album art, reusing as cover",
        len(playlist.track_files),
    )
    _log("Reusing shared album art as cover")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(first_art_bytes)
        tmp_path = Path(tmp.name)

    try:
        resize_cover(tmp_path, playlist.cover_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return True


def generate_cover_if_missing(
    playlist: "Playlist",
    log: "Callable[[str], None] | None" = None,
) -> None:
    """Generate a cover image for the playlist if one doesn't already exist."""
    if playlist.has_cover:
        logger.debug("generate_cover: skipping, cover already exists for '%s'", playlist.title)
        return
    logger.debug("generate_cover: generating for '%s'", playlist.title)

    if try_shared_album_art(playlist, log=log):
        return

    track_titles: list[str] = []
    artists: list[str] = []

    for filename in playlist.track_files:
        track_path = playlist.path / filename
        try:
            tags = mka.read_tags(track_path)
            title = tags.get("title") or Path(filename).stem
            artist = tags.get("artist", "")
        except Exception:
            title = Path(filename).stem
            artist = ""

        track_titles.append(title)
        if artist:
            artists.append(artist)

    prompt = build_cover_prompt(playlist.description, track_titles, artists, playlist.title)
    logger.debug("generate_cover prompt: %s", prompt)

    provider = get_provider()
    logger.debug("generate_cover: using provider %s", type(provider).__name__)
    # Request 3:4 aspect — wider than our 638:1011 target (~0.63), so
    # resize_cover crops the sides and preserves the full height including
    # title text at the top.
    image_bytes = provider.generate(prompt, 768, 1024)
    logger.debug("generate_cover: generated %d bytes", len(image_bytes))

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = Path(tmp.name)

    try:
        resize_cover(tmp_path, playlist.cover_path)
    finally:
        tmp_path.unlink(missing_ok=True)
