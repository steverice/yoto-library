"""Image manipulation, ICNS building, and pixel-art downscaling."""

from __future__ import annotations

import io
import struct

from PIL import Image

# ── Constants ────────────────────────────────────────────────────────────────

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


# ── Image helpers ────────────────────────────────────────────────────────────


def nearest_neighbor_upscale(img: Image.Image, target_size: int) -> Image.Image:
    """Resize img to target_size x target_size using nearest-neighbor to preserve crisp pixel grid."""
    return img.resize((target_size, target_size), Image.NEAREST)


def _color_distance(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """Manhattan distance between two RGB colors."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def remove_solid_background(
    img: Image.Image,
    threshold: float = 0.5,
    tolerance: int = 80,
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
    group_total = sum(c for color, c in counts.items() if _color_distance(color[:3], dominant[:3]) <= tolerance)

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


# ── ICNS builder ─────────────────────────────────────────────────────────────


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
