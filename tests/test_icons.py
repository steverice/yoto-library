"""Tests for icon pipeline (icons.py)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from yoto_lib.icons import (
    ICNS_SIZES,
    ICNS_TYPE_MAP,
    ICON_SIZE,
    generate_icns_sizes,
    match_public_icon,
    nearest_neighbor_upscale,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_16x16(color: str = "red") -> Image.Image:
    """Return a solid-color 16x16 RGBA image."""
    return Image.new("RGBA", (ICON_SIZE, ICON_SIZE), color=color)


def _make_checkerboard_16() -> Image.Image:
    """Return a 16x16 black-and-white checkerboard (1-pixel squares)."""
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE))
    pixels = img.load()
    for y in range(ICON_SIZE):
        for x in range(ICON_SIZE):
            pixels[x, y] = (255, 255, 255) if (x + y) % 2 == 0 else (0, 0, 0)
    return img


# ── TestNearestNeighborUpscale ────────────────────────────────────────────────


class TestNearestNeighborUpscale:
    def test_16_to_32_size(self):
        """Upscaling 16x16 to 32 produces a 32x32 image."""
        icon = _make_16x16("blue")
        result = nearest_neighbor_upscale(icon, 32)
        assert result.size == (32, 32)

    def test_checkerboard_preserves_pixel_grid(self):
        """Nearest-neighbor upscale of a checkerboard keeps hard pixel boundaries."""
        icon = _make_checkerboard_16()
        result = nearest_neighbor_upscale(icon, 32)
        assert result.size == (32, 32)

        pixels = result.load()
        # Each original pixel maps to a 2x2 block in the output.
        # Verify the top-left 4 output pixels match the 2x2 expected pattern:
        #   (0,0) orig → white  →  output (0,0),(1,0),(0,1),(1,1) all white
        #   (1,0) orig → black  →  output (2,0),(3,0),(2,1),(3,1) all black
        white = (255, 255, 255)
        black = (0, 0, 0)
        for dy in range(2):
            for dx in range(2):
                assert pixels[dx, dy] == white, f"pixel ({dx},{dy}) should be white"
                assert pixels[2 + dx, dy] == black, f"pixel ({2+dx},{dy}) should be black"


# ── TestGenerateIcnsSizes ─────────────────────────────────────────────────────


class TestGenerateIcnsSizes:
    def test_generates_all_sizes(self):
        """generate_icns_sizes returns an image for every size in ICNS_SIZES."""
        icon = _make_16x16()
        result = generate_icns_sizes(icon)

        assert set(result.keys()) == set(ICNS_SIZES)
        for size in ICNS_SIZES:
            assert result[size].size == (size, size), (
                f"Expected {size}x{size}, got {result[size].size}"
            )

    def test_all_sizes_covered_by_type_map(self):
        """Every size in ICNS_SIZES has a corresponding entry in ICNS_TYPE_MAP."""
        for size in ICNS_SIZES:
            assert size in ICNS_TYPE_MAP, f"Size {size} missing from ICNS_TYPE_MAP"
            assert len(ICNS_TYPE_MAP[size]) == 4, (
                f"Type tag for size {size} must be exactly 4 bytes"
            )


# ── TestMatchPublicIcon ───────────────────────────────────────────────────────


class TestMatchPublicIcon:
    def _icons(self, entries: list[tuple[str, str]]) -> list[dict]:
        """Build a list of public-icon dicts from (name, mediaId) pairs."""
        return [{"name": name, "mediaId": mid} for name, mid in entries]

    def test_exact_title_match(self):
        """An icon whose name exactly equals the track title returns its mediaId."""
        icons = self._icons([
            ("Jungle Adventure", "id-jungle"),
            ("Space Explorer", "id-space"),
        ])
        result = match_public_icon("Jungle Adventure", icons)
        assert result == "id-jungle"

    def test_partial_match(self):
        """An icon with significant word overlap is returned (score >= 0.5)."""
        icons = self._icons([
            ("Adventure Time", "id-adventure"),
            ("Bedtime Story", "id-bedtime"),
        ])
        # "Adventure Time Stories" shares "Adventure" and "Time" with first icon (2/3 ≈ 0.67)
        result = match_public_icon("Adventure Time Stories", icons)
        assert result == "id-adventure"

    def test_no_match_returns_none(self):
        """Returns None when no icon has a score >= 0.5."""
        icons = self._icons([
            ("Jungle Adventure", "id-jungle"),
            ("Space Explorer", "id-space"),
        ])
        result = match_public_icon("Completely Unrelated Topic Here", icons)
        assert result is None
