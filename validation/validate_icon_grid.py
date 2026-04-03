"""Validate the AI grid technique for generating 16x16 pixel art icons.

Generates a repeating 8x8 grid of identical icons on a 1024x1024 canvas using
an AI image provider (OpenAI gpt-image-1 or Google Gemini), then crops tiles
from three positions, downscales to 16x16 with two resampling filters, and
saves 256x256 preview images for inspection.

Usage:
    python validation/validate_icon_grid.py --provider openai "music note"
    python validation/validate_icon_grid.py --provider gemini "music note"

Output (written to validation/icon_output/):
    <prompt_slug>_grid.png           Full 1024x1024 generated grid
    <prompt_slug>_<pos>_16px.png     Raw 16x16 crop (nearest / lanczos)
    <prompt_slug>_<pos>_preview.png  256x256 nearest-neighbour upscale for inspection

Crop positions: top-left (tile 0,0), center (tile 3,3), bottom-right (tile 7,7)
Resampling: nearest-neighbor and lanczos
"""

from __future__ import annotations

import argparse
import base64
import io
import re
import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from PIL import Image
except ImportError:
    print("Error: Pillow is required. Install it with: pip install Pillow", file=sys.stderr)
    sys.exit(1)

OUTPUT_DIR = Path(__file__).resolve().parent / "icon_output"
GRID_SIZE = 8          # 8x8 tiles
CANVAS_SIZE = 1024     # pixels
TILE_SIZE = CANVAS_SIZE // GRID_SIZE   # 128px per tile
ICON_SIZE = 16         # final downscale target
PREVIEW_SIZE = 256     # upscale for visual inspection

# Tiles to sample: (label, grid_col, grid_row)
SAMPLE_TILES = [
    ("top_left",     0, 0),
    ("center",       3, 3),
    ("bottom_right", 7, 7),
]

GRID_PROMPT_TEMPLATE = (
    "A perfectly aligned 8x8 grid of identical 16x16 pixel art icons on a solid black background. "
    "Each icon is '{subject}'. "
    "Bold shapes, flat colors, high contrast, no gradients, no anti-aliasing. "
    "Every tile is identical and exactly the same size. "
    "The icon is simple enough to be clearly readable at 16x16 pixels. "
    "No borders, no labels, no gaps between tiles. "
    "The grid fills the entire image."
)


def slugify(text: str) -> str:
    """Convert a prompt to a safe filename fragment."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


# ── Provider implementations ──────────────────────────────────────────────────

def generate_with_openai(prompt: str) -> Image.Image:
    """Generate a 1024x1024 image using OpenAI gpt-image-1."""
    try:
        from openai import OpenAI
    except ImportError:
        print("Error: openai package not found. Install it with: pip install openai", file=sys.stderr)
        sys.exit(1)

    client = OpenAI()  # uses OPENAI_API_KEY from environment
    print("Sending prompt to OpenAI gpt-image-1 ...")
    response = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        n=1,
        size="1024x1024",
        response_format="b64_json",
    )
    b64_data = response.data[0].b64_json
    image_bytes = base64.b64decode(b64_data)
    return Image.open(io.BytesIO(image_bytes)).convert("RGBA")


def generate_with_gemini(prompt: str) -> Image.Image:
    """Generate a 1024x1024 image using Google Gemini."""
    try:
        import google.generativeai as genai
    except ImportError:
        print(
            "Error: google-generativeai package not found. "
            "Install it with: pip install google-generativeai",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Sending prompt to Google Gemini ...")
    model = genai.GenerativeModel("gemini-2.0-flash-preview-image-generation")
    response = model.generate_content(
        prompt,
        generation_config={"response_modalities": ["IMAGE", "TEXT"]},
    )

    # Walk response parts looking for inline image data
    for part in response.candidates[0].content.parts:
        if hasattr(part, "inline_data") and part.inline_data is not None:
            mime = part.inline_data.mime_type
            if mime and mime.startswith("image/"):
                image_bytes = part.inline_data.data
                return Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    # Fallback: try response.parts directly (older SDK layout)
    if hasattr(response, "parts"):
        for part in response.parts:
            if hasattr(part, "inline_data") and part.inline_data is not None:
                mime = getattr(part.inline_data, "mime_type", "")
                if mime.startswith("image/"):
                    return Image.open(io.BytesIO(part.inline_data.data)).convert("RGBA")

    raise RuntimeError(
        "Gemini response contained no inline image data. "
        "Check that the model supports image generation and the API key has the right permissions."
    )


# ── Image processing ───────────────────────────────────────────────────────────

def crop_tile(grid_image: Image.Image, col: int, row: int, tile_px: int) -> Image.Image:
    """Crop a single tile from the grid by (col, row) index."""
    x = col * tile_px
    y = row * tile_px
    return grid_image.crop((x, y, x + tile_px, y + tile_px))


def downscale(tile: Image.Image, size: int, resample: Image.Resampling) -> Image.Image:
    """Downscale a tile to size x size using the given resampling filter."""
    return tile.resize((size, size), resample=resample)


def upscale_preview(icon: Image.Image, size: int) -> Image.Image:
    """Upscale a tiny icon to size x size using nearest-neighbor for crisp pixels."""
    return icon.resize((size, size), resample=Image.Resampling.NEAREST)


def save_png(image: Image.Image, path: Path) -> None:
    image.save(str(path), format="PNG")
    print(f"  Saved: {path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate AI grid technique for 16x16 pixel art icon generation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "subject",
        metavar="<prompt>",
        help='Subject of the icon, e.g. "music note" or "rocket ship"',
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "gemini"],
        default="openai",
        help="Image generation provider (default: openai)",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(args.subject)
    prompt = GRID_PROMPT_TEMPLATE.format(subject=args.subject)

    print(f"Provider : {args.provider}")
    print(f"Subject  : {args.subject}")
    print(f"Output   : {OUTPUT_DIR}")
    print()

    # Generate grid image
    try:
        if args.provider == "openai":
            grid_image = generate_with_openai(prompt)
        else:
            grid_image = generate_with_gemini(prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILURE: Image generation error: {exc}", file=sys.stderr)
        return 1

    # Ensure it is exactly CANVAS_SIZE x CANVAS_SIZE
    if grid_image.size != (CANVAS_SIZE, CANVAS_SIZE):
        print(f"  Note: resizing from {grid_image.size} to ({CANVAS_SIZE}, {CANVAS_SIZE})")
        grid_image = grid_image.resize(
            (CANVAS_SIZE, CANVAS_SIZE), resample=Image.Resampling.LANCZOS
        )

    # Save the full grid
    grid_path = OUTPUT_DIR / f"{slug}_grid.png"
    save_png(grid_image, grid_path)

    # Process each sample tile
    resamplers = [
        ("nearest", Image.Resampling.NEAREST),
        ("lanczos", Image.Resampling.LANCZOS),
    ]

    print()
    for label, col, row in SAMPLE_TILES:
        tile = crop_tile(grid_image, col, row, TILE_SIZE)
        print(f"Tile [{label}] (grid col={col}, row={row}, {TILE_SIZE}x{TILE_SIZE}px):")

        for filter_name, resample in resamplers:
            icon_16 = downscale(tile, ICON_SIZE, resample)
            preview = upscale_preview(icon_16, PREVIEW_SIZE)

            icon_path = OUTPUT_DIR / f"{slug}_{label}_{filter_name}_16px.png"
            preview_path = OUTPUT_DIR / f"{slug}_{label}_{filter_name}_preview.png"

            save_png(icon_16, icon_path)
            save_png(preview, preview_path)

        print()

    print("DONE. Open the *_preview.png files to inspect icon quality.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
