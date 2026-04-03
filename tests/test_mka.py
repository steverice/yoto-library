import subprocess
from pathlib import Path

import pytest

from yoto_lib.mka import (
    wrap_in_mka,
    read_tags,
    write_tags,
    get_attachment,
    set_attachment,
    remove_attachment,
    probe_audio,
    TAG_MAP,
)


def ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def mkvtoolnix_available():
    try:
        subprocess.run(["mkvmerge", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")
needs_mkvtoolnix = pytest.mark.skipif(not mkvtoolnix_available(), reason="mkvtoolnix not installed")


class TestTagMap:
    def test_standard_tags_mapped(self):
        assert TAG_MAP["artist"] == "ARTIST"
        assert TAG_MAP["language"] == "LANGUAGE"
        assert TAG_MAP["title"] == "TITLE"

    def test_custom_yoto_tags_mapped(self):
        assert TAG_MAP["min_age"] == "YOTO_MIN_AGE"
        assert TAG_MAP["max_age"] == "YOTO_MAX_AGE"
        assert TAG_MAP["category"] == "YOTO_CATEGORY"
        assert TAG_MAP["read_by"] == "YOTO_READ_BY"


class TestWrapInMka:
    @needs_ffmpeg
    def test_wraps_wav_in_mka(self, sample_wav, tmp_path):
        output = tmp_path / "output.mka"
        wrap_in_mka(sample_wav, output)
        assert output.exists()
        assert output.stat().st_size > 0

    @needs_ffmpeg
    def test_output_is_matroska(self, sample_wav, tmp_path):
        output = tmp_path / "output.mka"
        wrap_in_mka(sample_wav, output)
        info = probe_audio(output)
        assert info["format"] == "matroska"

    def test_raises_on_missing_input(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            wrap_in_mka(tmp_path / "nonexistent.mp3", tmp_path / "out.mka")


class TestReadWriteTags:
    @needs_ffmpeg
    @needs_mkvtoolnix
    def test_write_and_read_tags(self, sample_wav, tmp_path):
        mka = tmp_path / "tagged.mka"
        wrap_in_mka(sample_wav, mka)

        tags = {"artist": "Test Artist", "title": "Test Song", "language": "en"}
        write_tags(mka, tags)

        result = read_tags(mka)
        assert result["artist"] == "Test Artist"
        assert result["title"] == "Test Song"
        assert result["language"] == "en"

    @needs_ffmpeg
    @needs_mkvtoolnix
    def test_write_custom_yoto_tags(self, sample_wav, tmp_path):
        mka = tmp_path / "tagged.mka"
        wrap_in_mka(sample_wav, mka)

        tags = {"min_age": "3", "max_age": "8", "category": "music"}
        write_tags(mka, tags)

        result = read_tags(mka)
        assert result["min_age"] == "3"
        assert result["max_age"] == "8"
        assert result["category"] == "music"


class TestAttachments:
    @needs_ffmpeg
    @needs_mkvtoolnix
    def test_set_and_get_icon_attachment(self, sample_wav, tmp_path):
        mka = tmp_path / "with_icon.mka"
        wrap_in_mka(sample_wav, mka)

        icon_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        icon_path = tmp_path / "icon.png"
        icon_path.write_bytes(icon_data)

        set_attachment(mka, icon_path, name="icon", mime_type="image/png")
        extracted = get_attachment(mka, name="icon")
        assert extracted is not None
        assert extracted == icon_data

    @needs_ffmpeg
    @needs_mkvtoolnix
    def test_set_gif_attachment(self, sample_wav, tmp_path):
        mka = tmp_path / "with_gif.mka"
        wrap_in_mka(sample_wav, mka)

        gif_data = b"GIF89a" + b"\x00" * 100
        gif_path = tmp_path / "icon.gif"
        gif_path.write_bytes(gif_data)

        set_attachment(mka, gif_path, name="icon", mime_type="image/gif")
        extracted = get_attachment(mka, name="icon")
        assert extracted is not None
        assert extracted[:6] == b"GIF89a"

    @needs_ffmpeg
    @needs_mkvtoolnix
    def test_get_attachment_returns_none_when_missing(self, sample_wav, tmp_path):
        mka = tmp_path / "no_icon.mka"
        wrap_in_mka(sample_wav, mka)

        assert get_attachment(mka, name="icon") is None

    @needs_ffmpeg
    @needs_mkvtoolnix
    def test_remove_attachment(self, sample_wav, tmp_path):
        mka = tmp_path / "removable.mka"
        wrap_in_mka(sample_wav, mka)

        icon_path = tmp_path / "icon.png"
        icon_path.write_bytes(b"\x89PNG" + b"\x00" * 50)
        set_attachment(mka, icon_path, name="icon", mime_type="image/png")
        assert get_attachment(mka, name="icon") is not None

        remove_attachment(mka, name="icon")
        assert get_attachment(mka, name="icon") is None
