"""AI icon generation: Retro Diffusion and grid-based fallback."""

from __future__ import annotations

import contextlib
import io
import logging
from typing import TYPE_CHECKING

import httpx
from PIL import Image

from yoto_lib.icons.download import ICON_CACHE_DIR
from yoto_lib.icons.image import (
    ICON_SIZE,
    _dominant_color_downscale,
    remove_solid_background,
)
from yoto_lib.mka import sanitize_filename as _sanitize_title
from yoto_lib.providers.base import check_status_on_error

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

try:
    from yoto_lib.providers.retrodiffusion_provider import RetroDiffusionProvider
except ImportError:
    RetroDiffusionProvider = None  # type: ignore[assignment,misc]

# ── Constants ────────────────────────────────────────────────────────────────

GRID_SIZE = 8
TILE_SIZE = 128  # 1024 / 8
CANVAS_SIZE = 1024


# ── Prompts ──────────────────────────────────────────────────────────────────


def _build_pixelart_prompt(visual_description: str) -> str:
    """Wrap a visual description in pixel-art style instructions."""
    return (
        f"Create a simple pixel art icon depicting: {visual_description}. "
        f"Style: very low resolution pixel art, maximum 6-8 colors, large blocky shapes. "
        f"Think original Game Boy or early NES sprite — extremely chunky pixels, no fine detail. "
        f"The subject must fill the entire canvas edge to edge — no empty margins, no padding, no whitespace around the subject. "  # noqa: E501
        f"Use a solid black (#000000) background. "
        f"No text, letters, numbers, or lettering. No anti-aliasing. No gradients. "
        f"Emoji style, bright colors, simple"
    )


def build_icon_prompt(track_title: str) -> str:
    """Build a prompt for generating an 8x8 grid of identical 16x16-style icons."""
    return (
        f"Generate an 8x8 grid of identical icons on a 1024x1024 pixel canvas. "
        f"Each icon is 128x128 pixels. Every cell in the grid shows the exact same icon. "
        f"The icon depicts: {track_title}. "
        f"Style: bold simple shapes, flat solid colors, minimal detail, high contrast. "
        f"Suitable for a 16x16 pixel icon when downscaled. "
        f"The subject must fill the entire icon area edge to edge — no empty margins, no padding, no whitespace around the subject. "  # noqa: E501
        f"Do not include any text, letters, numbers, or lettering."
    )


# ── Grid technique ───────────────────────────────────────────────────────────


def crop_icon_from_grid(img: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Crop the center tile from an 8x8 grid image and downscale to 16x16.

    Returns (tile_128x128, icon_16x16).
    """
    center = GRID_SIZE // 2
    left = center * TILE_SIZE
    top = center * TILE_SIZE
    tile = img.crop((left, top, left + TILE_SIZE, top + TILE_SIZE)).convert("RGB")
    icon_16 = _dominant_color_downscale(tile, ICON_SIZE)
    return tile, icon_16


def generate_raw_grid(track_title: str) -> bytes | None:
    """Generate the raw 1024x1024 grid image. Returns PNG bytes or None.

    Also saves the raw image to ~/.cache/yoto/icons/raw/ for inspection.
    """
    try:
        from yoto_lib.providers import get_provider

        provider = get_provider()
    except (ImportError, ValueError):
        return None

    prompt = build_icon_prompt(track_title)

    try:
        image_bytes = provider.generate(prompt, CANVAS_SIZE, CANVAS_SIZE)
    except (OSError, httpx.HTTPError):
        return None

    try:
        raw_dir = ICON_CACHE_DIR / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{_sanitize_title(track_title)}.png").write_bytes(image_bytes)
    except OSError:
        pass

    return image_bytes


# ── Retro Diffusion ──────────────────────────────────────────────────────────


def generate_retrodiffusion_icon(track_title: str) -> tuple[bytes | None, bytes | None]:
    """Generate via Retro Diffusion at native 16x16. Returns (raw_bytes, icon_16_bytes).

    Retro Diffusion is purpose-built for pixel art and generates true 16x16 output.
    No downscaling needed — the raw output IS the icon.
    Checks the cache first to avoid unnecessary API calls.
    """
    raw_dir = ICON_CACHE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_path = raw_dir / f"{_sanitize_title(track_title)}_retrodiffusion.png"

    if cache_path.exists():
        logger.debug("generate_retrodiffusion_icon: cache hit for '%s'", track_title)
        image_bytes = cache_path.read_bytes()
    else:
        logger.debug("generate_retrodiffusion_icon: generating for '%s'", track_title)
        from yoto_lib.providers.retrodiffusion_provider import RetroDiffusionProvider

        provider = RetroDiffusionProvider()
        prompt = _build_pixelart_prompt(track_title)

        try:
            image_bytes = provider.generate(prompt, ICON_SIZE, ICON_SIZE)
        except (OSError, httpx.HTTPError):
            return None, None

        with contextlib.suppress(OSError):
            cache_path.write_bytes(image_bytes)

    # The output IS 16x16 already — no downscaling needed
    # Flood-fill near-black background to transparent
    img = Image.open(io.BytesIO(image_bytes))
    img = remove_solid_background(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    icon_bytes = buf.getvalue()
    return image_bytes, icon_bytes


@check_status_on_error(RetroDiffusionProvider)
def generate_retrodiffusion_icons(
    descriptions: list[str],
    on_progress: Callable[[int], None] | None = None,
    on_icon_start: Callable[[int, str], None] | None = None,
    on_icon_done: Callable[[int], None] | None = None,
) -> list[tuple[bytes, Image.Image]]:
    """Generate one 16x16 icon per visual description via Retro Diffusion.

    Calls the API in parallel (one request per description) and reports
    progress as each completes.
    Returns list of (raw_bytes, processed_Image) pairs in input order.

    on_icon_start(i, description) is called before each icon's API call.
    on_icon_done(i) is called after each icon's API call completes.
    on_progress(done_count) is called after each icon completes (for backwards compat).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.debug("generate_retrodiffusion_icons: %d descriptions", len(descriptions))
    try:
        if RetroDiffusionProvider is None:
            return []
        provider = RetroDiffusionProvider()
    except (OSError, ValueError):
        return []

    def _generate_one(idx: int, desc: str) -> tuple[int, tuple[bytes, Image.Image] | None]:
        if on_icon_start:
            on_icon_start(idx, desc)
        prompt = _build_pixelart_prompt(desc)
        try:
            raw_bytes = provider.generate(prompt, ICON_SIZE, ICON_SIZE)
        except (OSError, httpx.HTTPError):
            if on_icon_done:
                on_icon_done(idx)
            return (idx, None)
        img = Image.open(io.BytesIO(raw_bytes))
        img = remove_solid_background(img)
        if on_icon_done:
            on_icon_done(idx)
        return (idx, (raw_bytes, img))

    # Submit all in parallel, track completion for progress
    ordered: dict[int, tuple[bytes, Image.Image] | None] = {}
    done_count = 0
    with ThreadPoolExecutor(max_workers=len(descriptions)) as pool:
        futures = [pool.submit(_generate_one, i, desc) for i, desc in enumerate(descriptions)]
        for future in as_completed(futures):
            idx, result = future.result()
            ordered[idx] = result
            done_count += 1
            if on_progress:
                on_progress(done_count)

    return [ordered[i] for i in range(len(descriptions)) if ordered.get(i) is not None]


def generate_track_icon(track_title: str) -> bytes | None:
    """Generate a 16x16 icon. Returns PNG bytes or None.

    Tries Retro Diffusion (native 16x16) first, falls back to the grid technique.
    """
    # Primary: Retro Diffusion — generates true 16x16 pixel art
    logger.debug("generate_track_icon: trying retrodiffusion for '%s'", track_title)
    try:
        _, icon_bytes = generate_retrodiffusion_icon(track_title)
        if icon_bytes:
            return icon_bytes
    except (OSError, httpx.HTTPError, ValueError):
        pass

    # Fallback: old grid technique (1024x1024 → crop → downscale)
    logger.debug("generate_track_icon: retrodiffusion failed, trying grid technique for '%s'", track_title)
    image_bytes = generate_raw_grid(track_title)
    if image_bytes is None:
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes))
        _, icon_16 = crop_icon_from_grid(img)
        buf = io.BytesIO()
        icon_16.save(buf, format="PNG")
        return buf.getvalue()
    except (OSError, ValueError):
        return None
