"""Round-trip integration tests: import → MKA → export produces byte-identical files."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from yoto_lib.mka import (
    PATCH_ATTACHMENT_NAME,
    apply_source_patch,
    extract_audio,
    generate_source_patch,
    get_attachment,
    set_attachment,
    wrap_in_mka,
    write_tags,
)


def _ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _mkvtoolnix_available():
    try:
        subprocess.run(["mkvmerge", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _bsdiff_available():
    return shutil.which("bsdiff") is not None


needs_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not installed")
needs_mkvtoolnix = pytest.mark.skipif(not _mkvtoolnix_available(), reason="mkvtoolnix not installed")
needs_bsdiff = pytest.mark.skipif(not _bsdiff_available(), reason="bsdiff not installed")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@needs_ffmpeg
@needs_mkvtoolnix
@needs_bsdiff
class TestRoundTrip:
    """Full round-trip: original → MKA → export → byte-identical original."""

    def _roundtrip(self, original: Path, tmp_path: Path) -> None:
        """Run the full import/export pipeline and assert byte-perfect identity."""
        original_hash = _sha256(original)
        mka_path = tmp_path / "track.mka"
        export_dir = tmp_path / "exported"
        export_dir.mkdir()

        # Import: wrap in MKA + write source format tag
        wrap_in_mka(original, mka_path)
        write_tags(mka_path, {"source_format": original.suffix.lstrip(".").lower()})

        # Generate bsdiff patch
        assert generate_source_patch(original, mka_path)

        # Verify patch is stored
        assert get_attachment(mka_path, PATCH_ATTACHMENT_NAME) is not None

        # Export: extract + apply patch
        extracted = extract_audio(mka_path, export_dir)
        final_path = export_dir / f"final{extracted.suffix}"
        assert apply_source_patch(extracted, mka_path, final_path)

        # Verify byte-perfect identity
        assert _sha256(final_path) == original_hash, (
            f"Round-trip failed for {original.name}: "
            f"original={original_hash[:16]}... exported={_sha256(final_path)[:16]}..."
        )

    def test_wav_roundtrip(self, longer_wav, tmp_path):
        self._roundtrip(longer_wav, tmp_path)

    def test_mp3_roundtrip(self, sample_mp3, tmp_path):
        self._roundtrip(sample_mp3, tmp_path)

    def test_m4a_roundtrip(self, sample_m4a, tmp_path):
        self._roundtrip(sample_m4a, tmp_path)

    def test_flac_roundtrip(self, sample_flac, tmp_path):
        self._roundtrip(sample_flac, tmp_path)

    def test_ogg_roundtrip(self, sample_ogg, tmp_path):
        self._roundtrip(sample_ogg, tmp_path)

    def test_alac_roundtrip(self, sample_alac, tmp_path):
        self._roundtrip(sample_alac, tmp_path)


@needs_ffmpeg
@needs_mkvtoolnix
@needs_bsdiff
class TestRoundTripAfterMutation:
    """Verify patch survives MKA mutations (tag edits, icon changes)."""

    def test_roundtrip_after_tag_and_icon_changes(self, sample_m4a, tmp_path):
        original_hash = _sha256(sample_m4a)
        mka_path = tmp_path / "track.mka"
        export_dir = tmp_path / "exported"
        export_dir.mkdir()

        # Import
        wrap_in_mka(sample_m4a, mka_path)
        write_tags(mka_path, {"source_format": "m4a"})
        assert generate_source_patch(sample_m4a, mka_path)

        # Mutate: add tags
        write_tags(
            mka_path,
            {
                "title": "Test Song",
                "artist": "Test Artist",
                "album": "Test Album",
            },
        )

        # Mutate: add icon attachment
        icon_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
        icon_path = tmp_path / "icon.png"
        icon_path.write_bytes(icon_data)
        set_attachment(mka_path, icon_path, name="icon", mime_type="image/png")

        # Export should still produce byte-perfect original
        extracted = extract_audio(mka_path, export_dir)
        final_path = export_dir / f"final{extracted.suffix}"
        assert apply_source_patch(extracted, mka_path, final_path)
        assert _sha256(final_path) == original_hash


@needs_ffmpeg
@needs_mkvtoolnix
class TestExtractWithoutPatch:
    """Export without a source.patch produces a playable file."""

    def test_extract_without_patch(self, sample_m4a, tmp_path):
        mka_path = tmp_path / "track.mka"
        export_dir = tmp_path / "exported"
        export_dir.mkdir()

        wrap_in_mka(sample_m4a, mka_path)
        write_tags(mka_path, {"source_format": "m4a"})
        # No patch generation

        extracted = extract_audio(mka_path, export_dir)
        assert extracted.exists()
        assert extracted.stat().st_size > 0
        assert extracted.suffix == ".m4a"

        # No patch to apply
        assert not apply_source_patch(extracted, mka_path, export_dir / "patched.m4a")


@needs_ffmpeg
@needs_mkvtoolnix
class TestExtractAudioFormats:
    """Verify extract_audio produces correct output format for each codec."""

    def test_extract_wav(self, longer_wav, tmp_path):
        mka = tmp_path / "track.mka"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        wrap_in_mka(longer_wav, mka)
        write_tags(mka, {"source_format": "wav"})
        out = extract_audio(mka, out_dir)
        assert out.suffix == ".wav"

    def test_extract_mp3(self, sample_mp3, tmp_path):
        mka = tmp_path / "track.mka"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        wrap_in_mka(sample_mp3, mka)
        write_tags(mka, {"source_format": "mp3"})
        out = extract_audio(mka, out_dir)
        assert out.suffix == ".mp3"

    def test_extract_m4a(self, sample_m4a, tmp_path):
        mka = tmp_path / "track.mka"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        wrap_in_mka(sample_m4a, mka)
        write_tags(mka, {"source_format": "m4a"})
        out = extract_audio(mka, out_dir)
        assert out.suffix == ".m4a"

    def test_extract_flac(self, sample_flac, tmp_path):
        mka = tmp_path / "track.mka"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        wrap_in_mka(sample_flac, mka)
        write_tags(mka, {"source_format": "flac"})
        out = extract_audio(mka, out_dir)
        assert out.suffix == ".flac"

    def test_extract_ogg(self, sample_ogg, tmp_path):
        mka = tmp_path / "track.mka"
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        wrap_in_mka(sample_ogg, mka)
        write_tags(mka, {"source_format": "ogg"})
        out = extract_audio(mka, out_dir)
        assert out.suffix == ".ogg"


@needs_ffmpeg
@needs_mkvtoolnix
class TestBsdiffNotAvailable:
    """When bsdiff is missing, import succeeds and export produces near-identical output."""

    def test_import_without_bsdiff(self, sample_m4a, tmp_path):
        mka_path = tmp_path / "track.mka"
        wrap_in_mka(sample_m4a, mka_path)
        write_tags(mka_path, {"source_format": "m4a"})

        # Mock bsdiff as unavailable
        with patch("shutil.which", return_value=None):
            result = generate_source_patch(sample_m4a, mka_path)

        assert result is False
        assert get_attachment(mka_path, PATCH_ATTACHMENT_NAME) is None

        # Export still works (just not byte-perfect)
        export_dir = tmp_path / "exported"
        export_dir.mkdir()
        extracted = extract_audio(mka_path, export_dir)
        assert extracted.exists()
        assert extracted.stat().st_size > 0
