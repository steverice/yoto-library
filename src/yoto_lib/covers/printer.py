"""Print cover art to a photo printer via macOS lpr + Pillow ICC."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from PIL import Image, ImageCms

logger = logging.getLogger(__name__)

# 54x86mm Selphy sticker sheet aspect ratio
PRINT_RATIO = 54 / 86  # 0.6279
ASPECT_TOLERANCE = 0.05  # 5% deviation allowed

DEFAULT_PRINTER = "Canon_SELPHY_CP1300"


class PrintError(Exception):
    """Error during the print pipeline."""


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
            f"cover.png has unexpected dimensions ({w}x{h}). Expected portrait aspect ratio close to 638x1011."
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
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PrintError(f"Printer '{printer}' not found. Configure it in System Settings > Printers & Scanners.")


def _icc_convert(img: Image.Image, icc_profile: str) -> Image.Image:
    """Apply ICC profile via Pillow/lcms2.

    Handles both standard printer profiles (prtr) and device link profiles (link).
    """
    try:
        if img.mode != "RGB":
            img = img.convert("RGB")
        profile = ImageCms.getOpenProfile(icc_profile)
        if profile.profile.device_class == "link":
            transform = ImageCms.buildTransform(profile, profile, "RGB", "RGB")
            return ImageCms.applyTransform(img, transform)  # ty: ignore[invalid-return-type]
        srgb = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(img, srgb, profile)  # ty: ignore[invalid-return-type]
    except (OSError, ImageCms.PyCMSError) as exc:
        raise PrintError(f"Color conversion failed: {exc}") from exc


def _send_to_printer(file_path: Path, printer: str) -> None:
    """Send a file to the printer via lpr."""
    result = subprocess.run(
        [
            "lpr",
            "-P",
            printer,
            "-o",
            "PageSize=54x86mm.Fullbleed",
            "-o",
            "fit-to-page",
            str(file_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PrintError(f"Print failed: {result.stderr.strip()}")


def _get_job_status(printer: str) -> str | None:
    """Get the current status of the active job on the printer.

    Returns the status string (e.g. "Looking for printer.", "Sending data"),
    or None if no job is in the queue.
    """
    result = subprocess.run(
        ["lpstat", "-l", "-o", printer],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    # Look for "Status: ..." line
    for line in result.stdout.splitlines():
        m = re.match(r"\s+Status:\s+(.+)", line)
        if m:
            return m.group(1).strip()
    # Job lines exist but no Status line means it's queued
    if result.stdout.strip():
        return "Queued"
    return None


def wait_for_job(
    printer: str,
    on_status: Callable[[str], None] | None = None,
    poll_interval: float = 1.0,
) -> None:
    """Poll CUPS until the active job on the printer completes.

    Calls on_status with the current status string each time it changes.
    Returns when no jobs remain in the queue.
    """
    last_status = None
    while True:
        status = _get_job_status(printer)
        if status is None:
            return  # Job complete — no longer in queue
        if status != last_status:
            last_status = status
            if on_status:
                on_status(status)
        time.sleep(poll_interval)


def print_cover(
    cover_path: Path,
    printer: str | None = None,
    icc_profile: str | None = None,
    on_status: Callable[[str], None] | None = None,
) -> None:
    """Full print pipeline: validate, crop, optionally ICC convert, print.

    Args:
        cover_path: Path to cover.png
        printer: CUPS printer name (default: YOTO_PRINTER env or Canon_SELPHY_CP1300)
        icc_profile: Path to ICC profile, or None to skip color management
        on_status: Called with CUPS job status updates while waiting for print
    """
    _check_platform()

    printer = printer or os.environ.get("YOTO_PRINTER", DEFAULT_PRINTER)
    _check_printer(printer)

    # Validate and crop
    img = validate_cover(cover_path)
    img = crop_for_print(img)

    # ICC convert if profile provided
    if icc_profile:
        img = _icc_convert(img, icc_profile)

    # Save to temp file and print
    print_tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            print_tmp = Path(f.name)
            img.save(f, format="PNG")

        _send_to_printer(print_tmp, printer)
        wait_for_job(printer, on_status=on_status)

    finally:
        if print_tmp:
            print_tmp.unlink(missing_ok=True)
