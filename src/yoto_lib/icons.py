"""Icon resolution for Yoto playlist tracks."""

from __future__ import annotations

import io
import logging
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from PIL import Image

from yoto_lib import mka

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from yoto_lib.api import YotoAPI
    from yoto_lib.playlist import Playlist

try:
    from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider
except Exception:
    RetroDiffusionProvider = None  # type: ignore[assignment,misc]

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


def _color_distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """Manhattan distance between two RGB colors."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def remove_solid_background(
    img: Image.Image, threshold: float = 0.5, tolerance: int = 80,
) -> Image.Image:
    """Flood-fill the dominant border color to transparency.

    Examines the outermost ring of pixels. Groups similar border colors
    (within *tolerance* Manhattan distance) to find the dominant background.
    Flood-fills from every matching border pixel inward, treating any color
    within *tolerance* of the dominant as background.
    """
    img = img.convert("RGBA")
    w, h = img.size

    # Collect border pixel positions and colors
    border_positions: list[tuple[int, int]] = []
    for x in range(w):
        border_positions.append((x, 0))
        border_positions.append((x, h - 1))
    for y in range(1, h - 1):
        border_positions.append((0, y))
        border_positions.append((w - 1, y))

    pixels = img.load()
    border_colors: list[tuple[int, ...]] = [pixels[x, y] for x, y in border_positions]

    # Group similar colors: count each color, then merge groups within tolerance
    counts: dict[tuple[int, ...], int] = {}
    for px in border_colors:
        counts[px] = counts.get(px, 0) + 1

    # Find the dominant group: start from most frequent color, absorb neighbors
    dominant = max(counts, key=counts.get)  # type: ignore[arg-type]
    group_total = sum(
        c for color, c in counts.items() if _color_distance(color[:3], dominant[:3]) <= tolerance
    )

    if group_total / len(border_colors) < threshold:
        return img  # no clear background color

    bg_rgb = dominant[:3]

    # Seed flood-fill from every border pixel within tolerance of dominant
    visited: set[tuple[int, int]] = set()
    queue: list[tuple[int, int]] = []
    for pos, color in zip(border_positions, border_colors):
        if _color_distance(color[:3], bg_rgb) <= tolerance:
            queue.append(pos)
            visited.add(pos)

    while queue:
        x, y = queue.pop()
        pixels[x, y] = (0, 0, 0, 0)
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                visited.add((nx, ny))
                if _color_distance(pixels[nx, ny][:3], bg_rgb) <= tolerance:
                    queue.append((nx, ny))

    return img



def generate_icns_sizes(icon_16: Image.Image) -> dict[int, Image.Image]:
    """Generate all ICNS sizes from a 16x16 source image using nearest-neighbor upscaling."""
    return {size: nearest_neighbor_upscale(icon_16, size) for size in ICNS_SIZES}


def _dominant_color_downscale(img: Image.Image, grid_size: int) -> Image.Image:
    """Downscale by taking the most common color in each cell.

    Divides *img* into a grid_size x grid_size grid of equal cells and picks
    the single most-frequent RGB value per cell.  This cleanly collapses
    anti-aliased pixel-art into hard-edged pixels.
    """
    w, h = img.size
    cell_w = w // grid_size
    cell_h = h // grid_size
    out = Image.new("RGB", (grid_size, grid_size))

    for gy in range(grid_size):
        for gx in range(grid_size):
            box = (gx * cell_w, gy * cell_h, (gx + 1) * cell_w, (gy + 1) * cell_h)
            cell = img.crop(box)
            # Count pixel frequencies
            colors: dict[tuple, int] = {}
            for pixel in cell.get_flattened_data():
                colors[pixel] = colors.get(pixel, 0) + 1
            dominant = max(colors, key=colors.get)  # type: ignore[arg-type]
            out.putpixel((gx, gy), dominant)

    return out


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
        logger.debug("download_icon: cache hit for %s", icon_hash)
        return cached.read_bytes()

    if icon_ref.startswith("http"):
        url = icon_ref
    else:
        url = f"{ICON_BASE_URL}/{icon_hash}"

    try:
        logger.debug("download_icon: fetching %s", icon_hash)
        data = _download_bytes(url)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data
    except Exception:
        return None


def apply_icon_to_mka(mka_path: Path, icon_data: bytes) -> None:
    """Attach icon PNG to MKA and set macOS Finder icon."""
    if mka_path.suffix.lower() == ".mka":
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


def _run_osascript(script: str) -> None:
    """Run an AppleScript via a temp file to avoid shell escaping issues."""
    with tempfile.NamedTemporaryFile(suffix=".scpt", mode="w", delete=False) as tmp:
        tmp.write(script)
        script_path = tmp.name
    try:
        subprocess.run(
            ["osascript", "-l", "AppleScript", script_path],
            capture_output=True,
            check=True,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)


def set_macos_file_icon(file_path: Path, icon_16: Image.Image) -> None:
    """Set the macOS Finder icon for file_path using an ICNS built from icon_16."""
    icns_data = build_icns(icon_16)

    with tempfile.NamedTemporaryFile(suffix=".icns", delete=False) as tmp:
        tmp.write(icns_data)
        icns_path = tmp.name

    try:
        abs_file = str(Path(file_path).resolve())
        script = _OSASCRIPT_TEMPLATE.format(
            icns_path=icns_path,
            file_path=abs_file,
        )
        _run_osascript(script)
    finally:
        Path(icns_path).unlink(missing_ok=True)


def clear_macos_file_icon(file_path: Path) -> None:
    """Remove the custom Finder icon from a file."""
    abs_file = str(Path(file_path).resolve())
    script = (
        f'use framework "AppKit"\n'
        f'use scripting additions\n'
        f'set ws to current application\'s NSWorkspace\'s sharedWorkspace()\n'
        f'ws\'s setIcon:(missing value) forFile:"{abs_file}" options:0\n'
    )
    _run_osascript(script)


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
        name: str = icon.get("title", "") or icon.get("name", "") or ""
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


def _sanitize_title(title: str) -> str:
    """Sanitize a title for use as a filename."""
    return title.replace("/", "-").replace(":", "-").replace("\0", "").strip()


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
        raw_dir = ICON_CACHE_DIR / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{_sanitize_title(track_title)}.png").write_bytes(image_bytes)
    except Exception:
        pass

    return image_bytes


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
    except Exception:
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
    except Exception:
        return None


# ── Strategy: pixelart (image gen with pixel-block prompt) ────────────────────


def _build_pixelart_prompt(visual_description: str) -> str:
    """Wrap a visual description in pixel-art style instructions."""
    return (
        f"Create a simple pixel art icon depicting: {visual_description}. "
        f"Style: very low resolution pixel art, maximum 6-8 colors, large blocky shapes. "
        f"Think original Game Boy or early NES sprite — extremely chunky pixels, no fine detail. "
        f"The subject should fill most of the canvas. "
        f"Use a solid black (#000000) background. "
        f"No text, letters, numbers, or lettering. No anti-aliasing. No gradients. "
        f"Emoji style, bright colors, simple"
    )


def generate_pixelart_icon(track_title: str) -> tuple[bytes | None, bytes | None]:
    """Generate a 16x16 icon via the pixel-art block technique.

    Returns (raw_1024_bytes, icon_16_bytes) — either may be None on failure.
    """
    try:
        from yoto_lib.image_providers import get_provider
        provider = get_provider()
    except Exception:
        return None, None

    prompt = _build_pixelart_prompt(track_title)

    try:
        image_bytes = provider.generate(prompt, CANVAS_SIZE, CANVAS_SIZE)
    except Exception:
        return None, None

    try:
        raw_dir = ICON_CACHE_DIR / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{_sanitize_title(track_title)}_pixelart.png").write_bytes(image_bytes)
    except Exception:
        pass

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        icon_16 = _dominant_color_downscale(img, ICON_SIZE)
        buf = io.BytesIO()
        icon_16.save(buf, format="PNG")
        return image_bytes, buf.getvalue()
    except Exception:
        return image_bytes, None


# ── Strategy: small-image providers (256x256) ────────────────────────────────


def _generate_small_icon(
    provider,
    track_title: str,
    width: int,
    height: int,
    label: str = "unknown",
) -> tuple[bytes | None, bytes | None]:
    """Generate an icon using a provider that supports small output sizes.

    Returns (raw_bytes, icon_16_bytes).
    """
    prompt = _build_pixelart_prompt(track_title)

    try:
        image_bytes = provider.generate(prompt, width, height)
    except Exception:
        return None, None

    try:
        raw_dir = ICON_CACHE_DIR / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{_sanitize_title(track_title)}_{label}.png").write_bytes(image_bytes)
    except Exception:
        pass

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        icon_16 = _dominant_color_downscale(img, ICON_SIZE)
        buf = io.BytesIO()
        icon_16.save(buf, format="PNG")
        return image_bytes, buf.getvalue()
    except Exception:
        return image_bytes, None


def generate_dalle2_icon(track_title: str) -> tuple[bytes | None, bytes | None]:
    """Generate via DALL-E 2 at 256x256. Returns (raw_bytes, icon_16_bytes)."""
    from yoto_lib.image_providers.dalle2_provider import DallE2Provider
    return _generate_small_icon(DallE2Provider(), track_title, 256, 256, "dalle2")


def generate_flux_icon(track_title: str) -> tuple[bytes | None, bytes | None]:
    """Generate via FLUX.1-schnell at 256x256. Returns (raw_bytes, icon_16_bytes)."""
    from yoto_lib.image_providers.together_provider import TogetherProvider
    return _generate_small_icon(
        TogetherProvider("black-forest-labs/FLUX.1-schnell"),
        track_title, 256, 256, "flux",
    )


def generate_sd3_icon(track_title: str) -> tuple[bytes | None, bytes | None]:
    """Generate via Stable Diffusion 3 at 256x256. Returns (raw_bytes, icon_16_bytes)."""
    from yoto_lib.image_providers.together_provider import TogetherProvider
    return _generate_small_icon(
        TogetherProvider("stabilityai/stable-diffusion-3-medium"),
        track_title, 256, 256, "sd3",
    )


def generate_gemini_icon(track_title: str) -> tuple[bytes | None, bytes | None]:
    """Generate via Gemini Imagen 4.0 (1024x1024). Returns (raw_bytes, icon_16_bytes)."""
    from yoto_lib.image_providers.gemini_provider import GeminiProvider
    return _generate_small_icon(GeminiProvider(), track_title, 1024, 1024, "gemini")


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
        from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider
        provider = RetroDiffusionProvider()
        prompt = _build_pixelart_prompt(track_title)

        try:
            image_bytes = provider.generate(prompt, ICON_SIZE, ICON_SIZE)
        except Exception:
            return None, None

        try:
            cache_path.write_bytes(image_bytes)
        except Exception:
            pass

    # The output IS 16x16 already — no downscaling needed
    # Flood-fill near-black background to transparent
    img = Image.open(io.BytesIO(image_bytes))
    img = remove_solid_background(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    icon_bytes = buf.getvalue()
    return image_bytes, icon_bytes


def generate_retrodiffusion_icons(
    descriptions: list[str],
    on_progress: "Callable[[int], None] | None" = None,
) -> list[tuple[bytes, Image.Image]]:
    """Generate one 16x16 icon per visual description via Retro Diffusion.

    Calls the API in parallel (one request per description) and reports
    progress as each completes.
    Returns list of (raw_bytes, processed_Image) pairs in input order.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.debug("generate_retrodiffusion_icons: %d descriptions", len(descriptions))
    try:
        if RetroDiffusionProvider is None:
            return []
        provider = RetroDiffusionProvider()
    except Exception:
        return []

    def _generate_one(desc: str) -> tuple[bytes, Image.Image] | None:
        prompt = _build_pixelart_prompt(desc)
        try:
            raw_bytes = provider.generate(prompt, ICON_SIZE, ICON_SIZE)
        except Exception:
            return None
        img = Image.open(io.BytesIO(raw_bytes))
        img = remove_solid_background(img)
        return (raw_bytes, img)

    # Submit all in parallel, track completion for progress
    ordered: dict[int, tuple[bytes, Image.Image] | None] = {}
    done_count = 0
    with ThreadPoolExecutor(max_workers=len(descriptions)) as pool:
        future_to_idx = {
            pool.submit(_generate_one, desc): i
            for i, desc in enumerate(descriptions)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            ordered[idx] = future.result()
            done_count += 1
            if on_progress:
                on_progress(done_count)

    return [ordered[i] for i in range(len(descriptions)) if ordered.get(i) is not None]


# ── Strategy: textmodel (Claude CLI / OpenAI text model) ─────────────────────


def _build_textmodel_prompt(track_title: str) -> str:
    """Prompt for a text model to output a 16x16 pixel grid as JSON."""
    return (
        f"Design a 16x16 pixel art icon that depicts: {track_title}\n\n"
        f"Output ONLY a JSON array of 16 rows, each containing 16 hex color strings.\n"
        f"Use a limited palette (8 colors max). Make bold, recognizable shapes.\n"
        f"Use a solid background color. The icon should read clearly at tiny size.\n\n"
        f"Example format (2x2):\n"
        f'[["#000000","#FF0000"],["#FF0000","#000000"]]\n\n'
        f"Now output the full 16x16 grid (16 rows of 16 hex colors). Output ONLY the JSON array, nothing else."
    )


def _parse_pixel_json(text: str) -> Image.Image | None:
    """Parse a JSON grid of hex colors into a 16x16 PIL Image."""
    import json as _json
    import re
    # Strip markdown code fences if present
    text = re.sub(r"```(?:json)?\s*", "", text)
    # Find the outermost JSON array — may be compact [[...]] or pretty-printed [\n  [...]\n]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        grid = _json.loads(text[start:end + 1])
    except _json.JSONDecodeError:
        return None

    if len(grid) != 16 or any(len(row) != 16 for row in grid):
        return None

    img = Image.new("RGB", (16, 16))
    for y, row in enumerate(grid):
        for x, color in enumerate(row):
            try:
                r = int(color[1:3], 16)
                g = int(color[3:5], 16)
                b = int(color[5:7], 16)
                img.putpixel((x, y), (r, g, b))
            except (ValueError, IndexError):
                pass
    return img


def generate_textmodel_icon_claude(track_title: str) -> bytes | None:
    """Generate a 16x16 icon by asking Claude CLI for pixel data."""
    import subprocess
    prompt = _build_textmodel_prompt(track_title)
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", "haiku"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return None
        # --output-format json wraps response in {"result": "..."}
        import json as _json
        try:
            wrapper = _json.loads(result.stdout)
            text = wrapper.get("result", result.stdout)
        except _json.JSONDecodeError:
            text = result.stdout

        img = _parse_pixel_json(text)
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def generate_textmodel_icon_openai(track_title: str) -> bytes | None:
    """Generate a 16x16 icon by asking GPT-4o for pixel data."""
    try:
        import openai
        client = openai.OpenAI()
    except Exception:
        return None

    prompt = _build_textmodel_prompt(track_title)
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        text = response.choices[0].message.content or ""
        img = _parse_pixel_json(text)
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
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
    logger.debug("_upload_icon_bytes: %d bytes", len(icon_bytes))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(icon_bytes)
        tmp_path = Path(tmp.name)
    try:
        upload_result = api.upload_icon(tmp_path, auto_convert=True)
        di = upload_result.get("displayIcon", upload_result)
        media_id = di.get("mediaId") or di.get("id")
        logger.debug("_upload_icon_bytes: -> mediaId=%s", media_id)
        return media_id
    except Exception as exc:
        logger.debug("_upload_icon_bytes: upload failed: %s", exc)
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def _pick_ai_icon(
    track_title: str,
    batch: "list[tuple[bytes, Image.Image]]",
    yoto_icon_bytes: "bytes | None" = None,
    yoto_media_id: "str | None" = None,
    descriptions: "list[str] | None" = None,
    album_description: "str | None" = None,
) -> "tuple[bytes | None, Image.Image | None, str | None]":
    """Use LLM to pick the best icon from AI candidates (+ optional Yoto icon).

    Returns (icon_bytes, icon_image, media_id_if_yoto_won).
    media_id_if_yoto_won is set only when the Yoto icon wins; otherwise None.
    """
    if not batch:
        return None, None, None

    raw_images = [raw for raw, _ in batch]
    winner, scores = compare_icons_llm(
        track_title, raw_images, yoto_icon=yoto_icon_bytes,
        descriptions=descriptions, album_description=album_description,
    )

    total = len(batch) + (1 if yoto_icon_bytes else 0)
    winner = max(1, min(winner, total))  # clamp to valid range

    if yoto_icon_bytes and winner == total:
        # Yoto icon won
        logger.debug("_pick_ai_icon: Yoto icon won for '%s'", track_title)
        yoto_img = Image.open(io.BytesIO(yoto_icon_bytes))
        return yoto_icon_bytes, yoto_img, yoto_media_id

    # AI icon won
    logger.debug("_pick_ai_icon: AI option %d won for '%s'", winner, track_title)
    idx = winner - 1
    raw_bytes, processed_img = batch[idx]
    buf = io.BytesIO()
    processed_img.save(buf, format="PNG")
    return buf.getvalue(), processed_img, None


def _read_album_description(playlist_path: Path) -> str | None:
    """Read description.txt from a playlist folder, if it exists."""
    desc_path = playlist_path / "description.txt"
    if desc_path.exists():
        return desc_path.read_text(encoding="utf-8")
    return None


def resolve_icons(
    playlist: "Playlist",
    api: "YotoAPI",
    log: "Callable[[str], None] | None" = None,
) -> dict[str, str]:
    """Resolve and embed icons for each track in the playlist.

    Resolution order:
    1. MKA attachment named "icon" → already on disk, upload to API
    2. LLM match against Yoto public icon catalog:
       - High confidence (>= 0.8) → use Yoto icon directly
       - Gray zone (0.4 - 0.8) → generate 3 AI, LLM picks best of 4
       - Low confidence (< 0.4) → generate 3 AI, LLM picks best of 3
    3. Fallback: lexical match → single AI generation (if LLM unavailable)

    Returns dict mapping filename → mediaId.
    """
    _log = log or (lambda msg: None)
    result: dict[str, str] = {}
    catalog: "list[dict] | None" = None  # lazy-loaded
    total = len(playlist.track_files)
    logger.debug("resolve_icons: %d tracks", total)

    for i, filename in enumerate(playlist.track_files, 1):
        track_path = playlist.path / filename
        title = Path(filename).stem
        media_id: "str | None" = None
        icon_bytes: "bytes | None" = None

        # 1. Check for existing MKA icon attachment
        try:
            icon_bytes = mka.get_attachment(track_path, "icon")
        except Exception:
            icon_bytes = None

        if icon_bytes is not None:
            logger.debug("resolve_icons[%s]: found local MKA icon", filename)
            _log(f"Icon {i}/{total}: {title} (local)")
            media_id = _upload_icon_bytes(api, icon_bytes)
        else:
            track_title = _derive_track_title(track_path, filename)

            # Load catalog once
            if catalog is None:
                catalog = get_catalog(api)

            # Exact-match shortcut: skip LLM for obvious matches
            exact_match_id = None
            title_lower = track_title.lower()
            for icon in catalog:
                icon_title = (icon.get("title", "") or icon.get("name", "")).lower()
                if icon_title and icon_title == title_lower:
                    exact_match_id = icon.get("mediaId")
                    break

            if exact_match_id:
                logger.debug("resolve_icons[%s]: exact match -> mediaId=%s", filename, exact_match_id)
                _log(f"Icon {i}/{total}: {title} (exact match)")
                dl_bytes = download_icon(exact_match_id)
                if dl_bytes:
                    apply_icon_to_mka(track_path, dl_bytes)
                    icon_bytes = dl_bytes
                media_id = exact_match_id
                if media_id is not None:
                    if icon_bytes is not None:
                        try:
                            img = Image.open(io.BytesIO(icon_bytes))
                            set_macos_file_icon(track_path, img)
                        except Exception:
                            pass
                    result[filename] = media_id
                    continue

            # 2. LLM-based matching
            matched_id, confidence = match_icon_llm(track_title, catalog)
            logger.debug("resolve_icons[%s]: LLM match mediaId=%s confidence=%.2f", filename, matched_id, confidence)

            if matched_id and confidence >= CONFIDENCE_HIGH:
                # High confidence — use Yoto icon directly
                logger.debug("resolve_icons[%s]: high confidence, using Yoto icon", filename)
                _log(f"Icon {i}/{total}: {title} (matched, confidence: {confidence:.2f})")
                dl_bytes = download_icon(matched_id)
                if dl_bytes:
                    apply_icon_to_mka(track_path, dl_bytes)
                    icon_bytes = dl_bytes
                media_id = matched_id

            elif matched_id and confidence >= CONFIDENCE_LOW:
                # Gray zone — generate 3 AI, compare with Yoto icon
                logger.debug("resolve_icons[%s]: gray zone, generating AI candidates + Yoto", filename)
                _log(f"Icon {i}/{total}: {title} (comparing, confidence: {confidence:.2f})")
                yoto_bytes = download_icon(matched_id)
                album_desc = _read_album_description(playlist.path)
                descriptions = describe_icons_llm(track_title, album_description=album_desc)
                batch = generate_retrodiffusion_icons(descriptions) if descriptions else []

                icon_bytes_result, icon_img, yoto_won_id = _pick_ai_icon(
                    track_title, batch,
                    yoto_icon_bytes=yoto_bytes,
                    yoto_media_id=matched_id,
                    descriptions=descriptions,
                    album_description=album_desc,
                )

                if yoto_won_id:
                    _log(f"Icon {i}/{total}: {title} (compared, chose: yoto)")
                    if yoto_bytes:
                        apply_icon_to_mka(track_path, yoto_bytes)
                    icon_bytes = yoto_bytes
                    media_id = yoto_won_id
                elif icon_bytes_result:
                    _log(f"Icon {i}/{total}: {title} (compared, chose: AI)")
                    apply_icon_to_mka(track_path, icon_bytes_result)
                    icon_bytes = icon_bytes_result
                    media_id = _upload_icon_bytes(api, icon_bytes_result)
                    if media_id:
                        ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        (ICON_CACHE_DIR / f"{media_id}.png").write_bytes(icon_bytes_result)

            else:
                # Low confidence — generate 3 AI, pick best
                logger.debug("resolve_icons[%s]: low confidence, generating AI candidates", filename)
                _log(f"Icon {i}/{total}: {title} (generating...)")
                album_desc = _read_album_description(playlist.path)
                descriptions = describe_icons_llm(track_title, album_description=album_desc)
                batch = generate_retrodiffusion_icons(descriptions) if descriptions else []

                if batch:
                    icon_bytes_result, icon_img, _ = _pick_ai_icon(
                        track_title, batch,
                        descriptions=descriptions,
                        album_description=album_desc,
                    )
                    if icon_bytes_result:
                        apply_icon_to_mka(track_path, icon_bytes_result)
                        icon_bytes = icon_bytes_result
                        media_id = _upload_icon_bytes(api, icon_bytes_result)
                        if media_id:
                            ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                            (ICON_CACHE_DIR / f"{media_id}.png").write_bytes(icon_bytes_result)
                        _log(f"Icon {i}/{total}: {title} (generated)")

                if media_id is None:
                    # Fallback: old single-icon generation
                    logger.debug("resolve_icons[%s]: fallback to single generation", filename)
                    icon_bytes = generate_track_icon(track_title)
                    if icon_bytes:
                        apply_icon_to_mka(track_path, icon_bytes)
                        media_id = _upload_icon_bytes(api, icon_bytes)
                        if media_id:
                            ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                            (ICON_CACHE_DIR / f"{media_id}.png").write_bytes(icon_bytes)
                    else:
                        logger.debug("resolve_icons[%s]: no icon generated", filename)
                        _log(f"Icon {i}/{total}: {title} (no icon)")

        # Set macOS Finder icon
        if icon_bytes is not None and media_id is not None:
            try:
                img = Image.open(io.BytesIO(icon_bytes))
                set_macos_file_icon(track_path, img)
            except Exception:
                pass

        if media_id is not None:
            result[filename] = media_id

    return result


# Late imports — placed after all definitions to avoid circular dependency
# with icon_catalog.py (which imports download_icon from this module).
from yoto_lib.icon_catalog import get_catalog  # noqa: E402
from yoto_lib.icon_llm import (  # noqa: E402
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    compare_icons_llm,
    describe_icons_llm,
    match_icon_llm,
)
