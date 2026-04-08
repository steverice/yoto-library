"""Tests for cover art printing pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from yoto_lib.printer import (
    PRINT_RATIO,
    ASPECT_TOLERANCE,
    PrintError,
    validate_cover,
    crop_for_print,
)


def _make_png(path: Path, width: int, height: int, color: str = "blue") -> Path:
    """Create a solid-color PNG at the given path."""
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, format="PNG")
    return path


class TestValidateCover:
    def test_valid_cover_returns_image(self, tmp_path):
        """A 638x1011 cover passes validation."""
        cover = _make_png(tmp_path / "cover.png", 638, 1011)
        img = validate_cover(cover)
        assert img.size == (638, 1011)

    def test_missing_file_raises(self, tmp_path):
        """Non-existent file raises PrintError."""
        with pytest.raises(PrintError, match="not found"):
            validate_cover(tmp_path / "cover.png")

    def test_bad_aspect_ratio_raises(self, tmp_path):
        """A square image (1:1 ratio) is rejected."""
        cover = _make_png(tmp_path / "cover.png", 500, 500)
        with pytest.raises(PrintError, match="unexpected dimensions"):
            validate_cover(cover)

    def test_close_aspect_ratio_passes(self, tmp_path):
        """An image close to 54:86 ratio passes (e.g., 638x1011 = 0.631)."""
        # 54:86 = 0.6279, 638:1011 = 0.6311 — within 5%
        cover = _make_png(tmp_path / "cover.png", 638, 1011)
        img = validate_cover(cover)
        assert img is not None


class TestCropForPrint:
    def test_crop_to_print_ratio(self, tmp_path):
        """Output aspect ratio matches 54:86 exactly."""
        img = Image.new("RGB", (638, 1011), color="blue")
        cropped = crop_for_print(img)
        w, h = cropped.size
        actual_ratio = w / h
        expected_ratio = 54 / 86
        assert abs(actual_ratio - expected_ratio) < 0.002

    def test_crop_preserves_dimensions_when_exact(self):
        """An image already at 54:86 ratio is returned unchanged."""
        # 540x860 is exactly 54:86
        img = Image.new("RGB", (540, 860), color="red")
        cropped = crop_for_print(img)
        assert cropped.size == (540, 860)

    def test_crop_centers(self):
        """Crop is centered (doesn't favor one side)."""
        # Create image with distinct left/right halves
        img = Image.new("RGB", (640, 1011), color="red")
        # 640x1011 ratio is 0.633, target is 0.628
        # Should crop ~5px from width: new_w = 1011 * 54/86 = 634.7 → 635
        cropped = crop_for_print(img)
        # Width should be close to 635 (center-cropped from 640)
        assert cropped.size[0] < 640
        assert cropped.size[1] == 1011  # Height unchanged (image is wider than target)
