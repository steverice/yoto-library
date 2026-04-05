"""Cover image generation for Yoto playlists."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from yoto_lib.image_providers import get_provider
from yoto_lib import mka

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
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


def generate_cover_if_missing(playlist: "Playlist") -> None:
    """Generate a cover image for the playlist if one doesn't already exist."""
    if playlist.has_cover:
        logger.debug("generate_cover: skipping, cover already exists for '%s'", playlist.title)
        return
    logger.debug("generate_cover: generating for '%s'", playlist.title)

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
