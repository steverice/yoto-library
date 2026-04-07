"""Cover image generation for Yoto playlists."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
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
RECOMPOSE_MAX_ATTEMPTS = int(os.environ.get("YOTO_RECOMPOSE_ATTEMPTS", "3"))


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
    "IMPORTANT: Leave a generous margin at the top and bottom — do NOT place "
    "any text or important elements in the top or bottom 10% of the image."
)


def _crop_flux_result(recomposed_raw: bytes) -> bytes:
    """Center-crop FLUX output to exact cover dimensions."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(recomposed_raw)
        cropped = Path(tmp.name)
    try:
        resize_cover(cropped, cropped)
        return cropped.read_bytes()
    finally:
        cropped.unlink(missing_ok=True)


def reframe_album_art(
    art_bytes: bytes,
    output_path: Path,
    log: "Callable[[str], None] | None" = None,
    on_step: "Callable[[], None] | None" = None,
) -> None:
    """Reframe square album art into a portrait cover via AI recomposition.

    Generates FLUX candidates, evaluates each with Claude, runs text repair
    if needed. If nothing passes quality checks, Claude picks the best
    candidate and warns the user.
    """
    _log = log or (lambda msg: None)

    candidates: list[bytes] = []
    max_attempts = RECOMPOSE_MAX_ATTEMPTS
    debug_dir = Path(tempfile.mkdtemp(prefix="yoto-reframe-"))

    try:
        from yoto_lib.image_providers.flux_provider import FluxProvider
        provider = FluxProvider()

        for attempt in range(1, max_attempts + 1):
            _log(f"Recomposing album art for cover (attempt {attempt}/{max_attempts})...")
            recomposed_raw = provider.recompose(art_bytes, _RECOMPOSE_PROMPT, COVER_WIDTH, COVER_HEIGHT)
            candidate = _crop_flux_result(recomposed_raw)

            debug_path = debug_dir / f"attempt_{attempt}.png"
            debug_path.write_bytes(candidate)
            logger.debug("reframe_album_art: attempt %d -> %s", attempt, debug_path)

            if check_recompose_quality(art_bytes, candidate):
                _log("Quality check passed")
                if on_step:
                    on_step()
                output_path.write_bytes(candidate)
                return

            _log("Quality check failed")
            if on_step:
                on_step()
            candidates.append(candidate)

        # All FLUX attempts failed — try text repair on last attempt
        _log("All attempts had issues — repairing text...")
        repaired = repair_text(art_bytes, candidates[-1], log=log)
        repaired_path = debug_dir / "repaired.png"
        repaired_path.write_bytes(repaired)
        logger.debug("reframe_album_art: repaired -> %s", repaired_path)

        if check_recompose_quality(art_bytes, repaired):
            _log("Repaired version passed quality check")
            output_path.write_bytes(repaired)
            return

        candidates.append(repaired)

    except Exception as exc:
        logger.warning("reframe_album_art: recomposition failed: %s", exc)

    # Nothing passed — pick the best of what we have
    if candidates:
        _log("No candidate passed quality checks — picking best available...")
        best = pick_best_candidate(art_bytes, candidates, debug_dir)
        output_path.write_bytes(best)
        _log(
            "WARNING: Could not generate a high-quality cover. "
            "The result may have text or visual issues. "
            "Run 'yoto cover --force' to try again."
        )
    else:
        # Total failure (e.g. API down) — use padded as last resort
        _log("WARNING: AI recomposition failed entirely. Using padded fallback.")
        output_path.write_bytes(pad_to_cover(art_bytes))


def build_cover_prompt(
    description: str | None,
    track_titles: list[str],
    artists: list[str],
    playlist_title: str | None = None,
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
        "Create a portrait-oriented children's book illustration with NO text."
        " Leave clear, uncluttered space in the upper 25% of the image for a title"
        " to be added later — use soft sky, clouds, a simple background, or a gentle"
        " color gradient there. No words, letters, or signs anywhere in the image."
    )

    return " ".join(parts)


def add_title_to_illustration(image_bytes: bytes, title: str, width: int, height: int) -> bytes:
    """Add a title to an illustration using OpenAI image editing.

    Passes the illustration to OpenAI's edit endpoint (no mask) and asks it
    to add the playlist title in a style matching the illustration.
    Returns PNG bytes at the same dimensions as input.

    Note: edit before resize — the API only accepts supported sizes
    (1024×1024, 1024×1536, 1536×1024), not the final 638×1011 cover.
    """
    from yoto_lib.image_providers.openai_provider import OpenAIProvider

    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")

    prompt = (
        f'Add the title "{title}" as a decorative banner in the upper portion of the image. '
        f"Use large, elegant lettering that matches the illustration's art style and color palette. "
        f"The title must be fully visible, horizontally centered, and well within the image boundaries — "
        f"leave clear margin on all sides. Keep the rest of the illustration unchanged."
    )

    provider = OpenAIProvider()
    # No mask — the model places the title naturally without painting outside the canvas.
    result = provider.edit(img_buf.getvalue(), b"", prompt, width, height)
    logger.debug("add_title_to_illustration: got %d bytes", len(result))
    return result


def try_shared_album_art(
    playlist: "Playlist",
    log: "Callable[[str], None] | None" = None,
    on_step: "Callable[[], None] | None" = None,
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

    reframe_album_art(first_art_bytes, playlist.cover_path, log=log, on_step=on_step)
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
    # Request 1024×1536 (2:3, ~0.667) — maps exactly to that OpenAI size,
    # only ~28px cropped per side to reach our 638:1011 (~0.631) target.
    image_bytes = provider.generate(prompt, 1024, 1536)
    logger.debug("generate_cover: generated %d bytes", len(image_bytes))

    # Add title via AI inpainting before resize (edit API needs supported dimensions).
    if playlist.title:
        logger.debug("generate_cover: adding title '%s' via inpainting", playlist.title)
        image_bytes = add_title_to_illustration(image_bytes, playlist.title, 1024, 1536)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = Path(tmp.name)

    try:
        resize_cover(tmp_path, playlist.cover_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def check_recompose_quality(original: bytes, recomposed: bytes) -> bool:
    """Ask Claude to check text and visual quality of a recomposed cover.

    Checks for: mangled/missing text, unnaturally stretched elements,
    distorted faces or objects. Returns True if acceptable, False if not.
    Falls back to True (accept) on failure.
    """
    with tempfile.TemporaryDirectory(prefix="yoto-text-check-") as tmpdir:
        tmp = Path(tmpdir)
        orig_path = tmp / "original.png"
        recomp_path = tmp / "recomposed.png"
        orig_path.write_bytes(original)
        recomp_path.write_bytes(recomposed)

        prompt = (
            f"Compare these two album cover images.\n\n"
            f"Original: {orig_path}\n"
            f"Recomposed: {recomp_path}\n\n"
            f"Answer NO if ANY of these problems exist:\n"
            f"- Text is missing, garbled, misspelled, or cut off at edges\n"
            f"- Characters or objects are unnaturally stretched or distorted\n"
            f"- Faces look wrong or deformed\n\n"
            f"Minor repositioning or style differences are fine.\n"
            f"If the original has no text, only check for distortion.\n\n"
            f"Reply with ONLY: YES or NO"
        )

        response = _call_claude(prompt, allowed_tools="Read", model="sonnet")

        if response:
            match = re.search(r"\b(YES|NO)\b", response.upper())
            if match:
                result = match.group(1) == "YES"
                logger.info("check_recompose_quality: %s", "pass" if result else "fail")
                return result

        logger.info("check_recompose_quality: defaulting to pass")
        return True


def describe_album_text(art_bytes: bytes) -> list[dict] | None:
    """Use Claude Sonnet to describe text and its visual style from album art.

    Returns list of dicts with keys: text, font, color, size, position, orientation.
    Returns None on failure.
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(art_bytes)
        tmp = f.name
    try:
        response = _call_claude(
            f"Read this album cover image: {tmp}\n"
            f"Describe ALL visible text and its visual style. For each text element:\n"
            f"- text: the exact text\n"
            f"- font: serif/sans-serif, bold/light, italic, etc.\n"
            f"- color: be specific (e.g. \"dark gray\", \"white\", \"yellow-green\")\n"
            f"- size: small/medium/large relative to the image\n"
            f"- position: e.g. \"top-right corner\", \"bottom-center\"\n"
            f"- orientation: horizontal or vertical\n\n"
            f"Skip any parental advisory or rating labels.\n"
            f"Return ONLY a JSON array. No commentary.",
            allowed_tools="Read",
            model="sonnet",
        )
        if response:
            match = re.search(r"\[[\s\S]*\]", response)
            if match:
                result = json.loads(match.group())
                logger.debug("describe_album_text: %s", result)
                return result
    except Exception as exc:
        logger.warning("describe_album_text: failed: %s", exc)
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


def render_text_layer(original: bytes, text_descriptions: list[dict]) -> bytes | None:
    """Use Gemini to render album text on a black background.

    Takes structured text descriptions (from describe_album_text) and renders
    them styled on black. Shows Gemini the original art for style reference.
    Returns PNG bytes or None on failure.
    """
    # Build rendering instructions from descriptions
    instructions = []
    for t in text_descriptions:
        instructions.append(
            f'- "{t["text"]}" in {t.get("color", "white")} {t.get("font", "")} font, '
            f'{t.get("size", "medium")} size, {t.get("position", "center")}, '
            f'{t.get("orientation", "horizontal")}'
        )

    if not instructions:
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[
                f"Generate an image with ONLY text on a pure black (#000000) "
                f"background. Render these text elements:\n"
                + "\n".join(instructions)
                + "\n\nNothing else in the image — only the text on solid black.",
                types.Part.from_bytes(data=original, mime_type="image/png"),
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        if not response.candidates:
            feedback = getattr(response, "prompt_feedback", None)
            logger.warning("render_text_layer: no candidates (feedback=%s)", feedback)
            return None

        candidate = response.candidates[0]
        finish = getattr(candidate, "finish_reason", None)
        if finish and str(finish) not in ("STOP", "0", "FinishReason.STOP"):
            logger.warning("render_text_layer: finish_reason=%s", finish)

        if not candidate.content or not candidate.content.parts:
            logger.warning("render_text_layer: empty content in candidate")
            return None

        for part in candidate.content.parts:
            if part.inline_data is not None:
                logger.debug("render_text_layer: %d bytes", len(part.inline_data.data))
                from yoto_lib.costs import get_tracker
                get_tracker().record("gemini_flash_image")
                return part.inline_data.data
            if hasattr(part, "text") and part.text:
                logger.debug("render_text_layer: got text instead of image: %s", part.text[:200])
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

    # Scale to fit within placement box
    pw, ph = placement["width"], placement["height"]
    scale = min(pw / text_cropped.width, ph / text_cropped.height)
    tw = max(1, int(text_cropped.width * scale))
    th = max(1, int(text_cropped.height * scale))
    text_final = text_cropped.resize((tw, th), Image.LANCZOS)

    # Center on placement box center
    cx = placement["x"] + pw // 2 - tw // 2
    cy = placement["y"] + ph // 2 - th // 2
    # Clamp to image bounds
    cx = max(0, min(cx, flux_img.width - tw))
    cy = max(0, min(cy, flux_img.height - th))

    logger.debug("composite_text: placing %dx%d at (%d, %d)", tw, th, cx, cy)
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
    text_descriptions = describe_album_text(original)
    if not text_descriptions:
        logger.warning("repair_text: text description failed, keeping recomposed as-is")
        return recomposed

    logger.debug("repair_text: found %d text elements", len(text_descriptions))

    text_layer = render_text_layer(original, text_descriptions)
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


def pick_best_candidate(
    original: bytes, candidates: list[bytes], debug_dir: Path,
) -> bytes:
    """Ask Claude to pick the best candidate from a set of imperfect options.

    Returns the chosen candidate's bytes. Falls back to the last candidate.
    """
    with tempfile.TemporaryDirectory(prefix="yoto-pick-best-") as tmpdir:
        tmp = Path(tmpdir)
        orig_path = tmp / "original.png"
        orig_path.write_bytes(original)

        file_list = [f"Original album art: {orig_path}"]
        for i, cand in enumerate(candidates, 1):
            p = tmp / f"candidate_{i}.png"
            p.write_bytes(cand)
            file_list.append(f"Candidate {i}: {p}")

        prompt = (
            f"Compare these album cover candidates.\n\n"
            + "\n".join(file_list)
            + f"\n\nWhich candidate looks best as an album cover? Consider:\n"
            f"- Is the text readable and correctly spelled?\n"
            f"- Are characters/objects undistorted?\n"
            f"- Is it visually appealing as a portrait cover?\n\n"
            f"Reply with ONLY the candidate number (1-{len(candidates)})"
        )

        response = _call_claude(prompt, allowed_tools="Read", model="sonnet")

        if response:
            match = re.search(r"\b(\d+)\b", response)
            if match:
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(candidates):
                    logger.info("pick_best_candidate: chose candidate %d", idx + 1)
                    # Save the chosen one to debug dir
                    chosen_path = debug_dir / f"chosen_{idx + 1}.png"
                    chosen_path.write_bytes(candidates[idx])
                    logger.debug("pick_best_candidate: -> %s", chosen_path)
                    return candidates[idx]

    logger.warning("pick_best_candidate: falling back to last candidate")
    return candidates[-1]


