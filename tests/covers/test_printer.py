"""Tests for cover art printing pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageCms

from yoto_lib.covers.printer import (
    PrintError,
    _check_platform,
    _check_printer,
    _get_job_status,
    _icc_convert,
    _send_to_printer,
    crop_for_print,
    print_cover,
    validate_cover,
    wait_for_job,
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
        img = Image.new("RGB", (540, 860), color="red")
        cropped = crop_for_print(img)
        assert cropped.size == (540, 860)

    def test_crop_centers(self):
        """Crop is centered (doesn't favor one side)."""
        img = Image.new("RGB", (640, 1011), color="red")
        cropped = crop_for_print(img)
        assert cropped.size[0] < 640
        assert cropped.size[1] == 1011


class TestCheckPlatform:
    def test_darwin_passes(self):
        """No error on macOS."""
        with patch("yoto_lib.covers.printer.sys") as mock_sys:
            mock_sys.platform = "darwin"
            _check_platform()

    def test_linux_raises(self):
        """Non-macOS raises PrintError."""
        with patch("yoto_lib.covers.printer.sys") as mock_sys:
            mock_sys.platform = "linux"
            with pytest.raises(PrintError, match="only supported on macOS"):
                _check_platform()


class TestCheckPrinter:
    def test_printer_found(self):
        """No error when lpstat succeeds."""
        with patch("yoto_lib.covers.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _check_printer("Canon_SELPHY_CP1300")
        mock_run.assert_called_once_with(
            ["lpstat", "-p", "Canon_SELPHY_CP1300"],
            capture_output=True,
            text=True,
        )

    def test_printer_not_found(self):
        """PrintError when lpstat fails."""
        with patch("yoto_lib.covers.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="not found")
            with pytest.raises(PrintError, match="not found"):
                _check_printer("NoSuchPrinter")


class TestIccConvert:
    def test_device_link_profile(self):
        """Device link profile uses buildTransform."""
        img = Image.new("RGB", (100, 160), color="blue")

        with patch("yoto_lib.covers.printer.ImageCms") as mock_cms:
            mock_profile = MagicMock()
            mock_profile.profile.device_class = "link"
            mock_transform = MagicMock()
            mock_cms.getOpenProfile.return_value = mock_profile
            mock_cms.buildTransform.return_value = mock_transform
            mock_cms.applyTransform.return_value = img

            _icc_convert(img, "/path/to/link.icc")

        mock_cms.buildTransform.assert_called_once_with(mock_profile, mock_profile, "RGB", "RGB")
        mock_cms.applyTransform.assert_called_once_with(img, mock_transform)

    def test_printer_profile(self):
        """Standard printer profile uses profileToProfile."""
        img = Image.new("RGB", (100, 160), color="blue")

        with patch("yoto_lib.covers.printer.ImageCms") as mock_cms:
            mock_profile = MagicMock()
            mock_profile.profile.device_class = "prtr"
            mock_cms.getOpenProfile.return_value = mock_profile
            mock_cms.createProfile.return_value = MagicMock()
            mock_cms.profileToProfile.return_value = img

            _icc_convert(img, "/path/to/printer.icc")

        mock_cms.createProfile.assert_called_once_with("sRGB")
        mock_cms.profileToProfile.assert_called_once()

    def test_profile_error_raises(self):
        """PrintError when ICC profile can't be applied."""
        img = Image.new("RGB", (100, 160), color="blue")

        with patch("yoto_lib.covers.printer.ImageCms") as mock_cms:
            mock_cms.PyCMSError = ImageCms.PyCMSError
            mock_cms.getOpenProfile.side_effect = OSError("bad profile")
            with pytest.raises(PrintError, match="Color conversion failed"):
                _icc_convert(img, "/bad/profile.icc")


class TestSendToPrinter:
    def test_calls_lpr_correctly(self, tmp_path):
        """lpr is called with correct printer, paper size, and fit-to-page."""
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG")

        with patch("yoto_lib.covers.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _send_to_printer(png, "Canon_SELPHY_CP1300")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "lpr"
        assert "-P" in cmd
        assert "Canon_SELPHY_CP1300" in cmd
        assert "PageSize=54x86mm.Fullbleed" in cmd
        assert "fit-to-page" in cmd

    def test_lpr_failure_raises(self, tmp_path):
        """PrintError when lpr returns non-zero."""
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG")

        with patch("yoto_lib.covers.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="offline")
            with pytest.raises(PrintError, match="Print failed"):
                _send_to_printer(png, "Canon_SELPHY_CP1300")


class TestGetJobStatus:
    def test_parses_status_line(self):
        """Extracts status from lpstat output."""
        output = "Canon_SELPHY_CP1300-385 smrice 1665024\n\tStatus: Looking for printer.\n\tAlerts: job-printing\n"
        with patch("yoto_lib.covers.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output)
            assert _get_job_status("Canon_SELPHY_CP1300") == "Looking for printer."

    def test_no_jobs_returns_none(self):
        """Returns None when no jobs in queue."""
        with patch("yoto_lib.covers.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            assert _get_job_status("Canon_SELPHY_CP1300") is None

    def test_job_without_status_returns_queued(self):
        """Returns 'Queued' when job exists but has no Status line."""
        output = "Canon_SELPHY_CP1300-385 smrice 1665024\n"
        with patch("yoto_lib.covers.printer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output)
            assert _get_job_status("Canon_SELPHY_CP1300") == "Queued"


class TestWaitForJob:
    def test_calls_on_status(self):
        """on_status is called with each new status."""
        statuses = []
        with (
            patch("yoto_lib.covers.printer._get_job_status", side_effect=["Queued", "Sending data", None]),
            patch("yoto_lib.covers.printer.time.sleep"),
        ):
            wait_for_job("Canon_SELPHY_CP1300", on_status=statuses.append)
        assert statuses == ["Queued", "Sending data"]

    def test_deduplicates_status(self):
        """Repeated same status only calls on_status once."""
        statuses = []
        with (
            patch("yoto_lib.covers.printer._get_job_status", side_effect=["Queued", "Queued", "Queued", None]),
            patch("yoto_lib.covers.printer.time.sleep"),
        ):
            wait_for_job("Canon_SELPHY_CP1300", on_status=statuses.append)
        assert statuses == ["Queued"]

    def test_returns_immediately_when_no_job(self):
        """Returns immediately if no job in queue."""
        with (
            patch("yoto_lib.covers.printer._get_job_status", return_value=None),
            patch("yoto_lib.covers.printer.time.sleep") as mock_sleep,
        ):
            wait_for_job("Canon_SELPHY_CP1300")
        mock_sleep.assert_not_called()


class TestPrintCover:
    def test_with_icc_profile(self, tmp_path):
        """print_cover applies ICC conversion when profile provided."""
        cover = _make_png(tmp_path / "cover.png", 638, 1011)
        fake_profile = tmp_path / "test.icc"
        fake_profile.write_bytes(b"fake")
        fake_img = Image.new("RGB", (635, 1011), "blue")

        with (
            patch("yoto_lib.covers.printer._check_platform"),
            patch("yoto_lib.covers.printer._check_printer"),
            patch("yoto_lib.covers.printer._icc_convert", return_value=fake_img) as mock_icc,
            patch("yoto_lib.covers.printer._send_to_printer") as mock_lpr,
        ):
            print_cover(cover, icc_profile=str(fake_profile))

        mock_icc.assert_called_once()
        mock_lpr.assert_called_once()

    def test_without_icc_profile(self, tmp_path):
        """print_cover skips ICC conversion when no profile provided."""
        cover = _make_png(tmp_path / "cover.png", 638, 1011)

        with (
            patch("yoto_lib.covers.printer._check_platform"),
            patch("yoto_lib.covers.printer._check_printer"),
            patch("yoto_lib.covers.printer._icc_convert") as mock_icc,
            patch("yoto_lib.covers.printer._send_to_printer") as mock_lpr,
        ):
            print_cover(cover)

        mock_icc.assert_not_called()
        mock_lpr.assert_called_once()

    def test_env_var_printer(self, tmp_path):
        """YOTO_PRINTER env var overrides default printer."""
        cover = _make_png(tmp_path / "cover.png", 638, 1011)

        with (
            patch("yoto_lib.covers.printer._check_platform"),
            patch("yoto_lib.covers.printer._check_printer") as mock_check,
            patch("yoto_lib.covers.printer._send_to_printer") as mock_lpr,
            patch.dict(os.environ, {"YOTO_PRINTER": "My_Printer"}),
        ):
            print_cover(cover)

        mock_check.assert_called_once_with("My_Printer")
        assert mock_lpr.call_args[0][1] == "My_Printer"
