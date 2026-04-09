"""Tests for icon helper functions: background removal, ICNS building, cropping, download/cache."""

from __future__ import annotations

import io
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from yoto_lib.icons import (
    ICON_SIZE,
    ICNS_SIZES,
    ICNS_TYPE_MAP,
    build_icns,
    crop_icon_from_grid,
    download_icon,
    remove_solid_background,
    _color_distance,
    _sanitize_title,
    apply_icon_to_mka,
)


# ── TestColorDistance ─────────────────────────────────────────────────────────


class TestColorDistance:
    def test_identical_colors(self):
        assert _color_distance((100, 100, 100), (100, 100, 100)) == 0

    def test_black_to_white(self):
        assert _color_distance((0, 0, 0), (255, 255, 255)) == 765

    def test_single_channel_diff(self):
        assert _color_distance((100, 0, 0), (200, 0, 0)) == 100


# ── TestRemoveSolidBackground ─────────────────────────────────────────────────


class TestRemoveSolidBackground:
    def test_removes_black_background(self):
        """A 16x16 icon with black border and red center — black should become transparent."""
        img = Image.new("RGB", (16, 16), color="black")
        # Paint a red 6x6 square in the center
        for y in range(5, 11):
            for x in range(5, 11):
                img.putpixel((x, y), (255, 0, 0))

        result = remove_solid_background(img, threshold=0.5, tolerance=80)
        assert result.mode == "RGBA"
        # Corner pixel should be fully transparent
        assert result.getpixel((0, 0))[3] == 0
        # Center pixel should remain opaque red
        assert result.getpixel((8, 8))[3] == 255
        assert result.getpixel((8, 8))[:3] == (255, 0, 0)

    def test_no_clear_background_returns_unchanged(self):
        """When no single color dominates the border, image is returned unchanged."""
        # Create a rainbow border — no single dominant color
        img = Image.new("RGBA", (8, 8))
        colors = [
            (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255),
            (255, 0, 255, 255), (0, 255, 255, 255), (128, 0, 0, 255), (0, 128, 0, 255),
        ]
        for x in range(8):
            img.putpixel((x, 0), colors[x % len(colors)])
            img.putpixel((x, 7), colors[(x + 1) % len(colors)])
        for y in range(1, 7):
            img.putpixel((0, y), colors[y % len(colors)])
            img.putpixel((7, y), colors[(y + 2) % len(colors)])
        # Fill interior with unique colors too
        for y in range(1, 7):
            for x in range(1, 7):
                img.putpixel((x, y), (x * 30, y * 30, 128, 255))

        result = remove_solid_background(img, threshold=0.5, tolerance=30)
        # Should be unchanged — no dominant background
        # Check a border pixel is still opaque
        assert result.getpixel((0, 0))[3] == 255


# ── TestBuildIcns ─────────────────────────────────────────────────────────────


class TestBuildIcns:
    def test_valid_icns_header(self):
        """ICNS data starts with 'icns' magic bytes and valid total length."""
        icon = Image.new("RGB", (16, 16), "red")
        data = build_icns(icon)
        assert data[:4] == b"icns"
        total_length = struct.unpack(">I", data[4:8])[0]
        assert total_length == len(data)

    def test_contains_all_size_entries(self):
        """Each expected size's type tag appears in the ICNS data."""
        icon = Image.new("RGB", (16, 16), "blue")
        data = build_icns(icon)
        for size in ICNS_SIZES:
            tag = ICNS_TYPE_MAP[size]
            assert tag in data, f"Missing type tag for size {size}"

    def test_icns_is_non_trivial_size(self):
        """The ICNS data should be substantially larger than the 16x16 source PNG."""
        icon = Image.new("RGB", (16, 16), "green")
        data = build_icns(icon)
        # With 6 sizes (16, 32, 64, 128, 256, 512), should be at least a few KB
        assert len(data) > 1000


# ── TestCropIconFromGrid ──────────────────────────────────────────────────────


class TestCropIconFromGrid:
    def test_crops_center_tile_and_downscales(self):
        """Cropping an 8x8 grid image returns a 128x128 tile and a 16x16 icon."""
        # Create a 1024x1024 image with a distinct center tile
        img = Image.new("RGB", (1024, 1024), color="blue")
        # Paint center tile (4*128=512 to 5*128=640) red
        center = 4
        left = center * 128
        top = center * 128
        for y in range(top, top + 128):
            for x in range(left, left + 128):
                img.putpixel((x, y), (255, 0, 0))

        tile, icon_16 = crop_icon_from_grid(img)
        assert tile.size == (128, 128)
        assert icon_16.size == (16, 16)
        # Center tile should be mostly red
        r, g, b = icon_16.getpixel((8, 8))
        assert r > 200 and g < 50 and b < 50

    def test_icon_is_rgb(self):
        """Output icon should always be in RGB mode."""
        img = Image.new("RGBA", (1024, 1024), color=(0, 0, 255, 128))
        tile, icon_16 = crop_icon_from_grid(img)
        assert icon_16.mode == "RGB"


# ── TestDownloadIcon ──────────────────────────────────────────────────────────


class TestDownloadIcon:
    def test_returns_from_cache(self, tmp_path):
        """Returns cached PNG when available, no HTTP call."""
        cache_dir = tmp_path / "icons"
        cache_dir.mkdir()
        (cache_dir / "abc123.png").write_bytes(b"\x89PNG cached")

        result = download_icon("abc123", cache_dir)
        assert result == b"\x89PNG cached"

    def test_downloads_and_caches_on_miss(self, tmp_path):
        """Fetches from network and saves to cache on miss."""
        cache_dir = tmp_path / "icons"

        with patch("yoto_lib.icons.download._download_bytes", return_value=b"\x89PNG fresh") as mock_dl:
            result = download_icon("def456", cache_dir)

        assert result == b"\x89PNG fresh"
        assert (cache_dir / "def456.png").read_bytes() == b"\x89PNG fresh"
        mock_dl.assert_called_once()

    def test_returns_none_on_network_error(self, tmp_path):
        """Returns None when download fails."""
        cache_dir = tmp_path / "icons"

        with patch("yoto_lib.icons.download._download_bytes", side_effect=OSError("timeout")):
            result = download_icon("ghi789", cache_dir)

        assert result is None

    def test_handles_yoto_hash_format(self, tmp_path):
        """Strips 'yoto:#' prefix when looking up cache."""
        cache_dir = tmp_path / "icons"
        cache_dir.mkdir()
        (cache_dir / "abc123.png").write_bytes(b"\x89PNG cached")

        result = download_icon("yoto:#abc123", cache_dir)
        assert result == b"\x89PNG cached"

    def test_empty_ref_returns_none(self, tmp_path):
        """Empty string returns None."""
        result = download_icon("", tmp_path)
        assert result is None


# ── TestSanitizeTitle ────────────────────────────────────────────────────────


class TestSanitizeTitle:
    def test_replaces_slashes_and_colons(self):
        assert _sanitize_title("path/to:file") == "path-to-file"

    def test_removes_null_bytes(self):
        assert _sanitize_title("file\x00name") == "filename"

    def test_strips_whitespace(self):
        assert _sanitize_title("  title  ") == "title"

    def test_clean_title_unchanged(self):
        assert _sanitize_title("Normal Title") == "Normal Title"


# ── TestApplyIconToMka ───────────────────────────────────────────────────────


class TestApplyIconToMka:
    def test_attaches_icon_to_mka(self, tmp_path):
        """apply_icon_to_mka calls set_attachment and set_macos_file_icon."""
        mka_path = tmp_path / "track.mka"
        mka_path.write_bytes(b"fake mka")

        # Create a valid small PNG
        img = Image.new("RGB", (16, 16), "red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        icon_data = buf.getvalue()

        with (
            patch("yoto_lib.icons.macos.mka.set_attachment") as mock_attach,
            patch("yoto_lib.icons.macos.set_macos_file_icon") as mock_set_icon,
        ):
            apply_icon_to_mka(mka_path, icon_data)

        mock_attach.assert_called_once()
        mock_set_icon.assert_called_once()

    def test_non_mka_file_skips_attachment(self, tmp_path):
        """Non-.mka files skip the MKA attachment but still set Finder icon."""
        mp3_path = tmp_path / "track.mp3"
        mp3_path.write_bytes(b"fake mp3")

        img = Image.new("RGB", (16, 16), "red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        icon_data = buf.getvalue()

        with (
            patch("yoto_lib.icons.macos.mka.set_attachment") as mock_attach,
            patch("yoto_lib.icons.macos.set_macos_file_icon") as mock_set_icon,
        ):
            apply_icon_to_mka(mp3_path, icon_data)

        mock_attach.assert_not_called()
        mock_set_icon.assert_called_once()
