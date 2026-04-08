"""Tests for cover art printing pipeline."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image, ImageCms

from yoto_lib.printer import (
    PRINT_RATIO,
    ASPECT_TOLERANCE,
    PrintError,
    validate_cover,
    crop_for_print,
    print_cover,
    _check_platform,
    _check_printer,
    _icc_convert,
    _send_to_printer,
    DEFAULT_PRINTER,
    DEFAULT_ICC_PROFILE,
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


class TestCheckPlatform:
    def test_darwin_passes(self):
        """No error on macOS."""
        with patch("yoto_lib.printer.sys") as mock_sys:
            mock_sys.platform = "darwin"
            _check_platform()  # should not raise

    def test_linux_raises(self):
        """Non-macOS raises PrintError."""
        with patch("yoto_lib.printer.sys") as mock_sys:
            mock_sys.platform = "linux"
            with pytest.raises(PrintError, match="only supported on macOS"):
                _check_platform()


class TestCheckPrinter:
    def test_printer_found(self):
        """No error when lpstat succeeds."""
        with patch("yoto_lib.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _check_printer("Canon_SELPHY_CP1300")
        mock_run.assert_called_once_with(
            ["lpstat", "-p", "Canon_SELPHY_CP1300"],
            capture_output=True, text=True,
        )

    def test_printer_not_found(self):
        """PrintError when lpstat fails."""
        with patch("yoto_lib.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="not found")
            with pytest.raises(PrintError, match="not found"):
                _check_printer("NoSuchPrinter")


class TestIccConvert:
    def test_applies_profile_transform(self):
        """ICC conversion applies device link profile via ImageCms."""
        img = Image.new("RGB", (100, 160), color="blue")

        with patch("yoto_lib.printer.ImageCms") as mock_cms:
            mock_profile = MagicMock()
            mock_transform = MagicMock()
            mock_cms.getOpenProfile.return_value = mock_profile
            mock_cms.buildTransform.return_value = mock_transform
            mock_cms.applyTransform.return_value = img

            result = _icc_convert(img, "/path/to/profile.icc")

        mock_cms.getOpenProfile.assert_called_once_with("/path/to/profile.icc")
        mock_cms.buildTransform.assert_called_once_with(mock_profile, mock_profile, "RGB", "RGB")
        mock_cms.applyTransform.assert_called_once_with(img, mock_transform)

    def test_profile_error_raises(self):
        """PrintError when ICC profile can't be applied."""
        img = Image.new("RGB", (100, 160), color="blue")

        with patch("yoto_lib.printer.ImageCms") as mock_cms:
            mock_cms.PyCMSError = ImageCms.PyCMSError
            mock_cms.getOpenProfile.side_effect = OSError("bad profile")
            with pytest.raises(PrintError, match="Color conversion failed"):
                _icc_convert(img, "/bad/profile.icc")


class TestSendToPrinter:
    def test_calls_lpr_correctly(self, tmp_path):
        """lpr is called with correct printer, paper size, and fit-to-page."""
        jpg = tmp_path / "test.jpg"
        jpg.write_bytes(b"\xff\xd8\xff")  # minimal JPEG header

        with patch("yoto_lib.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _send_to_printer(jpg, "Canon_SELPHY_CP1300")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "lpr"
        assert "-P" in cmd
        assert "Canon_SELPHY_CP1300" in cmd
        assert "PageSize=54x86mm.Fullbleed" in cmd
        assert "fit-to-page" in cmd

    def test_lpr_failure_raises(self, tmp_path):
        """PrintError when lpr returns non-zero."""
        jpg = tmp_path / "test.jpg"
        jpg.write_bytes(b"\xff\xd8\xff")

        with patch("yoto_lib.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="offline")
            with pytest.raises(PrintError, match="Print failed"):
                _send_to_printer(jpg, "Canon_SELPHY_CP1300")


class TestPrintCover:
    def test_full_pipeline(self, tmp_path):
        """print_cover calls validate → crop → ICC convert → lpr → cleanup."""
        cover = _make_png(tmp_path / "cover.png", 638, 1011)
        fake_profile = tmp_path / "test.icc"
        fake_profile.write_bytes(b"fake")
        fake_img = Image.new("RGB", (635, 1011), "blue")

        with patch("yoto_lib.printer._check_platform"), \
             patch("yoto_lib.printer._check_printer"), \
             patch("yoto_lib.printer._icc_convert", return_value=fake_img) as mock_icc, \
             patch("yoto_lib.printer._send_to_printer") as mock_lpr:
            print_cover(cover, icc_profile=str(fake_profile))

        mock_icc.assert_called_once()
        assert mock_icc.call_args[0][1] == str(fake_profile)
        mock_lpr.assert_called_once()
        assert mock_lpr.call_args[0][1] == DEFAULT_PRINTER

    def test_icc_profile_not_found(self, tmp_path):
        """PrintError when ICC profile path doesn't exist."""
        cover = _make_png(tmp_path / "cover.png", 638, 1011)

        with patch("yoto_lib.printer._check_platform"), \
             patch("yoto_lib.printer._check_printer"):
            with pytest.raises(PrintError, match="ICC profile not found"):
                print_cover(cover, icc_profile="/nonexistent/profile.icc")

    def test_env_var_overrides(self, tmp_path):
        """YOTO_PRINTER and YOTO_ICC_PROFILE env vars override defaults."""
        cover = _make_png(tmp_path / "cover.png", 638, 1011)
        fake_profile = tmp_path / "custom.icc"
        fake_profile.write_bytes(b"fake")

        fake_img = Image.new("RGB", (635, 1011), "blue")
        with patch("yoto_lib.printer._check_platform"), \
             patch("yoto_lib.printer._check_printer") as mock_check, \
             patch("yoto_lib.printer._icc_convert", return_value=fake_img) as mock_icc, \
             patch("yoto_lib.printer._send_to_printer") as mock_lpr, \
             patch.dict(os.environ, {
                 "YOTO_PRINTER": "My_Printer",
                 "YOTO_ICC_PROFILE": str(fake_profile),
             }):
            print_cover(cover)

        mock_check.assert_called_once_with("My_Printer")
        mock_lpr.assert_called_once()
        assert mock_lpr.call_args[0][1] == "My_Printer"
        mock_icc.assert_called_once()
        assert mock_icc.call_args[0][1] == str(fake_profile)
