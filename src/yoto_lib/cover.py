"""Cover image generation for Yoto playlists."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from yoto_lib.image_providers import get_provider
from yoto_lib import mka
from yoto_lib.icon_llm import _call_claude

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


def pad_to_cover(
    art_bytes: bytes,
    target_width: int = COVER_WIDTH,
    target_height: int = COVER_HEIGHT,
) -> bytes:
    """Scale album art to fit within target dimensions and pad with edge color.

    Returns PNG bytes of the padded image at target_width x target_height.
    """
    art = Image.open(io.BytesIO(art_bytes)).convert("RGB")

    scale = min(target_width / art.width, target_height / art.height)
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

    cover = Image.new("RGB", (target_width, target_height), (avg_r, avg_g, avg_b))
    x_offset = (target_width - new_w) // 2
    y_offset = (target_height - new_h) // 2
    cover.paste(scaled, (x_offset, y_offset))

    buf = io.BytesIO()
    cover.save(buf, format="PNG")
    return buf.getvalue()


_RECOMPOSE_PROMPT = (
    "A tall portrait version of this scene. Same characters, same art style, "
    "same colors, same mood. Keep all text exactly as it appears. "
    "Extend the scene into the black areas with detailed artwork — "
    "not a solid color fill."
)


def reframe_album_art(
    art_bytes: bytes,
    output_path: Path,
    log: "Callable[[str], None] | None" = None,
    style: str = "compare",
) -> None:
    """Reframe square album art into a portrait cover.

    style controls which candidate is used:
      - "compare": generate both, let Claude pick (default)
      - "ai": always use AI recomposition (fall back to padded on failure)
      - "pad": always use padded, skip AI
    """
    _log = log or (lambda msg: None)

    # Candidate A: simple padding
    padded = pad_to_cover(art_bytes)

    if style == "pad":
        _log("Using padded album art as cover")
        output_path.write_bytes(padded)
        return

    # Candidate B: AI recomposition via FLUX Kontext (retry up to 3 times for good text)
    recomposed = None
    max_attempts = 3
    debug_dir = Path(tempfile.mkdtemp(prefix="yoto-reframe-"))
    try:
        from yoto_lib.image_providers.flux_provider import FluxProvider
        provider = FluxProvider()
        for attempt in range(1, max_attempts + 1):
            _log(f"Recomposing album art for cover (attempt {attempt}/{max_attempts})...")
            recomposed_raw = provider.recompose(art_bytes, _RECOMPOSE_PROMPT, COVER_WIDTH, COVER_HEIGHT)
            recomposed = pad_to_cover(recomposed_raw)

            debug_path = debug_dir / f"attempt_{attempt}.png"
            debug_path.write_bytes(recomposed)
            logger.debug("reframe_album_art: attempt %d -> %s", attempt, debug_path)

            if check_text_quality(art_bytes, recomposed):
                _log("Text check passed")
                break
            _log("Text check failed")

            if attempt == max_attempts:
                _log("All attempts had text issues — repairing...")
                recomposed = repair_text(art_bytes, recomposed, log=log)
    except Exception as exc:
        logger.warning("reframe_album_art: recomposition failed: %s", exc)

    if style == "ai":
        result = recomposed if recomposed is not None else padded
        _log(f"Using {'recomposed' if recomposed is not None else 'padded (fallback)'} cover")
    elif recomposed is not None:
        winner = compare_covers(padded, recomposed)
        result = padded if winner == "a" else recomposed
        _log(f"Cover comparison: using {'padded' if winner == 'a' else 'recomposed'} version")
    else:
        result = padded
        _log("Using padded album art as cover")

    output_path.write_bytes(result)


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
    style: str = "compare",
) -> bool:
    """Check if all tracks share identical album art; if so, save it as the cover.

    Extracts embedded album art from each track's video stream, hashes them,
    and if all hashes match, reframes the shared art into a portrait cover.

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

    reframe_album_art(first_art_bytes, playlist.cover_path, log=log, style=style)
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


def check_text_quality(original: bytes, recomposed: bytes) -> bool:
    """Ask Claude to check if text from the original survives in the recomposed image.

    Returns True if text is intact, False if mangled/missing.
    Falls back to True (accept) on failure.
    """
    with tempfile.TemporaryDirectory(prefix="yoto-text-check-") as tmpdir:
        tmp = Path(tmpdir)
        orig_path = tmp / "original.png"
        recomp_path = tmp / "recomposed.png"
        orig_path.write_bytes(original)
        recomp_path.write_bytes(recomposed)

        prompt = (
            f"Compare the text in these two album cover images.\n\n"
            f"Original: {orig_path}\n"
            f"Recomposed: {recomp_path}\n\n"
            f"Is all visible text from the original image present and correctly "
            f"spelled in the recomposed image? Minor repositioning is fine — "
            f"only flag missing, garbled, or misspelled text.\n"
            f"If the original has no text, answer YES.\n\n"
            f"Reply with ONLY: YES or NO"
        )

        response = _call_claude(prompt, allowed_tools="Read", model="sonnet")

        if response:
            match = re.search(r"\b(YES|NO)\b", response.upper())
            if match:
                result = match.group(1) == "YES"
                logger.info("check_text_quality: %s", "pass" if result else "fail")
                return result

        logger.info("check_text_quality: defaulting to pass")
        return True


def ocr_album_text(art_bytes: bytes) -> str | None:
    """Use Claude Sonnet to OCR text from album art. Returns text or None."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(art_bytes)
        tmp = f.name
    try:
        response = _call_claude(
            f"Read this album cover image: {tmp}\n"
            f"List ALL visible text exactly as it appears, preserving "
            f"capitalization and punctuation. Return ONLY the text, "
            f"one line per text element. No commentary.",
            allowed_tools="Read",
            model="sonnet",
        )
        if response and response.strip():
            logger.debug("ocr_album_text: %r", response.strip())
            return response.strip()
    finally:
        Path(tmp).unlink(missing_ok=True)
    return None


def get_text_placement(
    original: bytes, recomposed: bytes, width: int, height: int,
) -> dict | None:
    """Ask Claude where to place text on the recomposed image.

    Returns dict with x, y, width, height keys, or None on failure.
    """
    with tempfile.TemporaryDirectory(prefix="yoto-placement-") as tmpdir:
        tmp = Path(tmpdir)
        orig_path = tmp / "original.png"
        recomp_path = tmp / "recomposed.png"
        orig_path.write_bytes(original)
        recomp_path.write_bytes(recomposed)

        response = _call_claude(
            f"Original album cover: {orig_path}\n"
            f"Portrait version ({width}x{height}): {recomp_path}\n\n"
            f"The portrait is missing text from the original. Where should I "
            f"place it? Match the original's relative position (same side/corner).\n\n"
            f'Return ONLY JSON: {{"x": <left>, "y": <top>, "width": <w>, "height": <h>}}',
            allowed_tools="Read",
            model="sonnet",
            timeout=180,
        )

        if response:
            match = re.search(r"\{[^}]+\}", response)
            if match:
                try:
                    placement = json.loads(match.group())
                    logger.debug("get_text_placement: %s", placement)
                    return placement
                except json.JSONDecodeError:
                    pass

    logger.warning("get_text_placement: failed")
    return None


def render_text_layer(original: bytes, ocr_text: str) -> bytes | None:
    """Use Gemini to render album text on a black background.

    Shows Gemini the original art for style reference and tells it the
    exact text to render. Returns PNG bytes or None on failure.
    """
    try:
        from google import genai
        from google.genai import types

        client = genai.Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[
                f"Look at the text styling in this album cover — the font, color, "
                f"size, weight, and position of each text element. "
                f"Generate a new image with ONLY this exact text on a pure black "
                f"(#000000) background:\n\n{ocr_text}\n\n"
                f"Match the font style, color, and approximate position from the "
                f"original. Spell the text EXACTLY as provided above.\n"
                f"Only render text on solid black — no artwork, no photos.",
                types.Part.from_bytes(data=original, mime_type="image/png"),
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                logger.debug("render_text_layer: %d bytes", len(part.inline_data.data))
                return part.inline_data.data
    except Exception as exc:
        logger.warning("render_text_layer: failed: %s", exc)

    return None


def composite_text(
    recomposed: bytes, text_layer: bytes, placement: dict,
) -> bytes:
    """Chroma-key text from black background and composite onto recomposed image.

    Crops the text layer to its bounding box, scales to 40% of image height,
    and centers it at the placement coordinates.
    """
    flux_img = Image.open(io.BytesIO(recomposed)).convert("RGBA")
    text_img = Image.open(io.BytesIO(text_layer)).convert("RGBA")

    # Chroma key: black → transparent, feather edges
    data = np.array(text_img)
    brightness = data[:, :, :3].astype(float).max(axis=2)
    data[brightness < 30, 3] = 0
    edge = (brightness >= 30) & (brightness < 60)
    data[edge, 3] = ((brightness[edge] - 30) / 30 * 255).clip(0, 255).astype(np.uint8)
    text_rgba = Image.fromarray(data)

    bbox = text_rgba.getbbox()
    if bbox is None:
        logger.warning("composite_text: no visible text in layer")
        return recomposed
    text_cropped = text_rgba.crop(bbox)

    # Scale to 40% of image height
    target_h = int(flux_img.height * 0.4)
    scale = target_h / text_cropped.height
    tw = max(1, int(text_cropped.width * scale))
    text_final = text_cropped.resize((tw, target_h), Image.LANCZOS)

    # Center on placement box center
    cx = placement["x"] + placement["width"] // 2 - tw // 2
    cy = placement["y"] + placement["height"] // 2 - target_h // 2
    # Clamp to image bounds
    cx = max(0, min(cx, flux_img.width - tw))
    cy = max(0, min(cy, flux_img.height - target_h))

    logger.debug("composite_text: placing %dx%d at (%d, %d)", tw, target_h, cx, cy)
    flux_img.paste(text_final, (cx, cy), text_final)

    buf = io.BytesIO()
    flux_img.save(buf, format="PNG")
    return buf.getvalue()


def repair_text(
    original: bytes,
    recomposed: bytes,
    log: "Callable[[str], None] | None" = None,
) -> bytes:
    """Repair missing/mangled text on a recomposed cover.

    Pipeline: OCR original → Gemini renders text on black → Claude picks
    placement → chroma key composite onto recomposed image.
    Falls back to the recomposed image unchanged on any failure.
    """
    _log = log or (lambda msg: None)

    _log("Repairing text on cover...")
    ocr_text = ocr_album_text(original)
    if not ocr_text:
        logger.warning("repair_text: OCR failed, keeping recomposed as-is")
        return recomposed

    logger.debug("repair_text: OCR text: %r", ocr_text)

    text_layer = render_text_layer(original, ocr_text)
    if not text_layer:
        logger.warning("repair_text: text rendering failed")
        return recomposed

    recomposed_img = Image.open(io.BytesIO(recomposed))
    placement = get_text_placement(
        original, recomposed, recomposed_img.width, recomposed_img.height,
    )
    if not placement:
        logger.warning("repair_text: placement failed")
        return recomposed

    result = composite_text(recomposed, text_layer, placement)
    _log("Text repaired on cover")
    return result


def compare_covers(padded: bytes, outpainted: bytes) -> str:
    """Ask Claude to compare two cover candidates and pick the better one.

    Returns 'a' (padded) or 'b' (outpainted). Falls back to 'b' on failure.
    """
    with tempfile.TemporaryDirectory(prefix="yoto-cover-compare-") as tmpdir:
        tmp = Path(tmpdir)
        a_path = tmp / "option_a_padded.png"
        b_path = tmp / "option_b_outpainted.png"
        a_path.write_bytes(padded)
        b_path.write_bytes(outpainted)

        prompt = (
            f"You are comparing two versions of an album cover reformatted for a "
            f"portrait frame.\n\n"
            f"Image A (padded): {a_path}\n"
            f"Image B (outpainted): {b_path}\n\n"
            f"Which looks better as an album cover? Consider:\n"
            f"- Does the original artwork look intact and unaltered?\n"
            f"- Does the background extension look natural?\n"
            f"- Is the overall result visually appealing?\n\n"
            f"Reply with ONLY the letter: A or B"
        )

        response = _call_claude(prompt, allowed_tools="Read", model="haiku")

        if response:
            # Look for a standalone A or B (not embedded in words like "BETTER")
            import re
            match = re.search(r"\b([AB])\b", response.upper())
            if match:
                choice = match.group(1).lower()
                logger.info("compare_covers: Claude chose %s version", "padded" if choice == "a" else "outpainted")
                return choice

        logger.info("compare_covers: defaulting to outpainted version (B)")
        return "b"
