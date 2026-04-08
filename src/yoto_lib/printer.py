"""Print cover art to a photo printer via macOS sips + lpr."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
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


def _check_platform() -> None:
    """Raise PrintError if not running on macOS."""
    if sys.platform != "darwin":
        raise PrintError("Printing is only supported on macOS.")


def _check_printer(printer: str) -> None:
    """Raise PrintError if the printer is not configured."""
    result = subprocess.run(
        ["lpstat", "-p", printer],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PrintError(
            f"Printer '{printer}' not found. "
            f"Configure it in System Settings > Printers & Scanners."
        )


def _icc_convert(input_path: Path, output_path: Path, icc_profile: str) -> None:
    """Convert image color space via macOS sips."""
    result = subprocess.run(
        [
            "sips",
            "-s", "format", "jpeg",
            "-s", "formatOptions", "100",
            "--matchToWithIntent", icc_profile, "relative",
            str(input_path),
            "--out", str(output_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PrintError(f"Color conversion failed: {result.stderr.strip()}")


def _send_to_printer(file_path: Path, printer: str) -> None:
    """Send a file to the printer via lpr."""
    result = subprocess.run(
        [
            "lpr",
            "-P", printer,
            "-o", "PageSize=54x86mm.Fullbleed",
            "-o", "fit-to-page",
            str(file_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PrintError(f"Print failed: {result.stderr.strip()}")


def print_cover(
    cover_path: Path,
    printer: str | None = None,
    icc_profile: str | None = None,
) -> None:
    """Full print pipeline: validate, crop, ICC convert, print.

    Args:
        cover_path: Path to cover.png
        printer: CUPS printer name (default: YOTO_PRINTER env or Canon_SELPHY_CP1300)
        icc_profile: Path to ICC profile (default: YOTO_ICC_PROFILE env or Canon Selphy CP1200.ICC)
    """
    _check_platform()

    printer = printer or os.environ.get("YOTO_PRINTER", DEFAULT_PRINTER)
    icc_profile = icc_profile or os.environ.get(
        "YOTO_ICC_PROFILE",
        os.path.expanduser(DEFAULT_ICC_PROFILE),
    )

    _check_printer(printer)

    if not Path(icc_profile).exists():
        raise PrintError(f"ICC profile not found: {icc_profile}")

    # Validate and crop
    img = validate_cover(cover_path)
    img = crop_for_print(img)

    # Save cropped image to temp PNG, then ICC-convert to JPEG
    cropped_tmp = None
    print_tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            cropped_tmp = Path(f.name)
            img.save(f, format="PNG")

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            print_tmp = Path(f.name)

        _icc_convert(cropped_tmp, print_tmp, icc_profile)
        _send_to_printer(print_tmp, printer)

    finally:
        if cropped_tmp:
            cropped_tmp.unlink(missing_ok=True)
        if print_tmp:
            print_tmp.unlink(missing_ok=True)
