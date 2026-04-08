"""Print cover art to a photo printer via macOS sips + lpr."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# 54x86mm Selphy sticker sheet aspect ratio
PRINT_RATIO = 54 / 86  # 0.6279
ASPECT_TOLERANCE = 0.05  # 5% deviation allowed

DEFAULT_PRINTER = "Canon_SELPHY_CP1300"
DEFAULT_ICC_PROFILE = "~/Library/ColorSync/Profiles/Canon Selphy CP1200.ICC"


class PrintError(Exception):
    """Error during the print pipeline."""
    pass


def validate_cover(cover_path: Path) -> Image.Image:
    """Validate cover exists and has expected aspect ratio.

    Returns the opened PIL Image on success.
    Raises PrintError if the file is missing or has wrong dimensions.
    """
    if not cover_path.exists():
        raise PrintError(f"Cover not found: {cover_path}")

    img = Image.open(cover_path)
    w, h = img.size
    if h == 0:
        raise PrintError(f"cover.png has unexpected dimensions ({w}x{h})")

    ratio = w / h
    if abs(ratio - PRINT_RATIO) / PRINT_RATIO > ASPECT_TOLERANCE:
        raise PrintError(
            f"cover.png has unexpected dimensions ({w}x{h}). "
            f"Expected portrait aspect ratio close to 638x1011."
        )
    return img


def crop_for_print(img: Image.Image) -> Image.Image:
    """Center-crop to exact 54:86 aspect ratio for Selphy sticker paper."""
    w, h = img.size
    ratio = w / h

    if abs(ratio - PRINT_RATIO) < 0.001:
        return img  # Already correct

    if ratio > PRINT_RATIO:
        # Image is wider than target: crop sides
        new_w = round(h * PRINT_RATIO)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        # Image is taller than target: crop top/bottom
        new_h = round(w / PRINT_RATIO)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))
