"""Cover image generation for Yoto playlists."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from yoto_lib.image_providers import get_provider
from yoto_lib import mka

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
) -> str:
    """Build a text prompt for image generation from playlist metadata."""
    parts: list[str] = []

    if description:
        parts.append(description.strip())

    titles = track_titles[:10]
    if titles:
        parts.append("Tracks: " + ", ".join(titles))

    unique_artists = list(dict.fromkeys(artists))  # deduplicate, preserve order
    if unique_artists:
        parts.append("Artists: " + ", ".join(unique_artists))

    parts.append(
        "Create a portrait illustration suitable for a children's audio card."
    )
    parts.append("Do not include any text, letters, or lettering in the image.")

    return " ".join(parts)


def generate_cover_if_missing(playlist: "Playlist") -> None:
    """Generate a cover image for the playlist if one doesn't already exist."""
    if playlist.has_cover:
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

    prompt = build_cover_prompt(playlist.description, track_titles, artists)

    provider = get_provider()
    image_bytes = provider.generate(prompt, COVER_WIDTH, COVER_HEIGHT)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = Path(tmp.name)

    try:
        resize_cover(tmp_path, playlist.cover_path)
    finally:
        tmp_path.unlink(missing_ok=True)
