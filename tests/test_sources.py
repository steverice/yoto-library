"""Tests for yoto_lib.sources — URL source resolution."""

from __future__ import annotations

import json
import plistlib
import struct
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from yoto_lib.sources import parse_webloc
from yoto_lib.sources.youtube import YouTubeProvider


class TestParseWebloc:
    def test_parse_webloc_extracts_url(self, tmp_path):
        """parse_webloc reads a .webloc plist and returns the URL string."""
        url = "https://www.youtube.com/watch?v=GxtknJ9KFKY"
        webloc = tmp_path / "song.webloc"
        webloc.write_bytes(plistlib.dumps({"URL": url}))

        assert parse_webloc(webloc) == url

    def test_parse_webloc_missing_url_key(self, tmp_path):
        """parse_webloc returns None when the plist has no URL key."""
        webloc = tmp_path / "bad.webloc"
        webloc.write_bytes(plistlib.dumps({"Name": "something"}))

        assert parse_webloc(webloc) is None

    def test_parse_webloc_invalid_plist(self, tmp_path):
        """parse_webloc returns None for a corrupt file."""
        webloc = tmp_path / "corrupt.webloc"
        webloc.write_bytes(b"this is not a plist")

        assert parse_webloc(webloc) is None


class TestYouTubeCanHandle:
    def setup_method(self):
        self.provider = YouTubeProvider()

    def test_standard_youtube_url(self):
        assert self.provider.can_handle("https://www.youtube.com/watch?v=GxtknJ9KFKY")

    def test_short_youtube_url(self):
        assert self.provider.can_handle("https://youtu.be/GxtknJ9KFKY")

    def test_youtube_music_url(self):
        assert self.provider.can_handle("https://music.youtube.com/watch?v=GxtknJ9KFKY")

    def test_youtube_shorts_url(self):
        assert self.provider.can_handle("https://www.youtube.com/shorts/GxtknJ9KFKY")

    def test_non_youtube_url(self):
        assert not self.provider.can_handle("https://www.example.com/video")

    def test_soundcloud_url(self):
        assert not self.provider.can_handle("https://soundcloud.com/artist/track")


def _make_wav(path: Path, duration_seconds: float = 1.0) -> None:
    """Write a minimal valid WAV file."""
    sample_rate = 44100
    num_samples = int(sample_rate * duration_seconds)
    data_size = num_samples * 2  # 16-bit mono
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate,
        sample_rate * 2, 2, 16,
        b"data", data_size,
    )
    path.write_bytes(header + b"\x00\x00" * num_samples)


class TestYouTubeDownload:
    def test_download_calls_ytdlp_and_returns_audio(self, tmp_path):
        """download invokes yt-dlp, returns the audio path and metadata."""
        provider = YouTubeProvider()
        url = "https://www.youtube.com/watch?v=GxtknJ9KFKY"

        def fake_run(cmd, **kwargs):
            if "--dump-json" in cmd:
                result = MagicMock()
                result.returncode = 0
                result.stdout = json.dumps({"title": "My Cool Song"})
                return result
            for i, arg in enumerate(cmd):
                if arg == "-o":
                    tmpl = cmd[i + 1]
                    out = Path(tmpl.replace("%(ext)s", "wav"))
                    _make_wav(out)
                    break
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("yoto_lib.sources.youtube.subprocess.run", side_effect=fake_run):
            audio_path, metadata = provider.download(url, tmp_path, trim=False)

        assert audio_path.exists()
        assert metadata["title"] == "My Cool Song"
        assert metadata["source_url"] == url

    def test_download_ytdlp_not_installed(self, tmp_path):
        """download raises RuntimeError when yt-dlp is not found."""
        provider = YouTubeProvider()

        with patch(
            "yoto_lib.sources.youtube.subprocess.run",
            side_effect=FileNotFoundError("No such file: 'yt-dlp'"),
        ):
            with pytest.raises(RuntimeError, match="yt-dlp is required"):
                provider.download("https://youtu.be/abc", tmp_path, trim=False)
