"""Tests for yoto_lib.sources — URL source resolution."""

from __future__ import annotations

import json
import plistlib
import struct
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from yoto_lib.sources import parse_webloc, resolve_weblocs
from yoto_lib.sources.youtube import YouTubeProvider, _parse_silence_ranges, _trim_silence


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


class TestSilenceDetection:
    def test_parse_silence_ranges_from_ffmpeg_output(self):
        """_parse_silence_ranges extracts (start, end) pairs from ffmpeg stderr."""
        stderr = (
            "[silencedetect @ 0x1234] silence_start: 0\n"
            "[silencedetect @ 0x1234] silence_end: 1.5 | silence_duration: 1.5\n"
            "[silencedetect @ 0x1234] silence_start: 30.2\n"
            "[silencedetect @ 0x1234] silence_end: 31.0 | silence_duration: 0.8\n"
            "[silencedetect @ 0x1234] silence_start: 65.0\n"
            "[silencedetect @ 0x1234] silence_end: 66.5 | silence_duration: 1.5\n"
        )
        ranges = _parse_silence_ranges(stderr)
        assert ranges == [(0.0, 1.5), (30.2, 31.0), (65.0, 66.5)]

    def test_parse_silence_ranges_empty(self):
        """_parse_silence_ranges returns empty list when no silence found."""
        assert _parse_silence_ranges("some random output\n") == []

    def test_parse_silence_ranges_unterminated(self):
        """A silence_start without a matching silence_end is ignored."""
        stderr = (
            "[silencedetect @ 0x1234] silence_start: 0\n"
            "[silencedetect @ 0x1234] silence_end: 1.5 | silence_duration: 1.5\n"
            "[silencedetect @ 0x1234] silence_start: 60.0\n"
        )
        ranges = _parse_silence_ranges(stderr)
        assert ranges == [(0.0, 1.5)]


class TestTrimSilence:
    def test_trim_with_two_gaps_extracts_middle(self, tmp_path):
        """When >=2 silence gaps, trim extracts audio between first and last gap."""
        audio = tmp_path / "song.wav"
        _make_wav(audio, duration_seconds=2.0)

        silence_stderr = (
            "[silencedetect @ 0x1234] silence_start: 0\n"
            "[silencedetect @ 0x1234] silence_end: 0.5 | silence_duration: 0.5\n"
            "[silencedetect @ 0x1234] silence_start: 1.5\n"
            "[silencedetect @ 0x1234] silence_end: 2.0 | silence_duration: 0.5\n"
        )

        call_count = [0]
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if call_count[0] == 0:
                # silencedetect call
                result.stderr = silence_stderr
                call_count[0] += 1
                return result
            else:
                # ffmpeg trim call — write a trimmed file
                for i, arg in enumerate(cmd):
                    if i > 0 and cmd[i - 1] == "-ss":
                        assert arg == "0.5"  # start after first silence
                for i, arg in enumerate(cmd):
                    if i > 0 and cmd[i - 1] == "-to":
                        assert arg == "1.5"  # end at start of last silence
                # Find output path (last argument)
                out = Path(cmd[-1])
                _make_wav(out, duration_seconds=1.0)
                result.returncode = 0
                return result

        with patch("yoto_lib.sources.youtube.subprocess.run", side_effect=fake_run):
            trimmed = _trim_silence(audio)

        assert trimmed == audio  # same path, file replaced in-place
        assert trimmed.exists()

    def test_trim_with_fewer_than_two_gaps_returns_original(self, tmp_path):
        """When <2 silence gaps, return original file untouched."""
        audio = tmp_path / "clean.wav"
        _make_wav(audio, duration_seconds=2.0)

        silence_stderr = (
            "[silencedetect @ 0x1234] silence_start: 0\n"
            "[silencedetect @ 0x1234] silence_end: 0.3 | silence_duration: 0.3\n"
        )

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stderr = silence_stderr
            return result

        with patch("yoto_lib.sources.youtube.subprocess.run", side_effect=fake_run):
            trimmed = _trim_silence(audio)

        assert trimmed == audio  # unchanged


class TestResolveWeblocs:
    def test_resolve_webloc_downloads_and_wraps_mka(self, tmp_path):
        """A .webloc with a YouTube URL is resolved into an .mka, webloc deleted."""
        playlist_dir = tmp_path / "My Playlist"
        playlist_dir.mkdir()

        url = "https://www.youtube.com/watch?v=GxtknJ9KFKY"
        webloc = playlist_dir / "song.webloc"
        webloc.write_bytes(plistlib.dumps({"URL": url}))

        fake_audio = tmp_path / "temp_audio.opus"
        _make_wav(fake_audio)

        mock_provider = MagicMock()
        mock_provider.can_handle.return_value = True
        mock_provider.download.return_value = (fake_audio, {"title": "My Song", "source_url": url})

        with (
            patch("yoto_lib.sources._get_providers", return_value=[mock_provider]),
            patch("yoto_lib.sources.wrap_in_mka") as mock_wrap,
            patch("yoto_lib.sources.write_tags") as mock_tags,
        ):
            def fake_wrap(src, dst):
                dst.write_bytes(b"fake mka")
            mock_wrap.side_effect = fake_wrap

            results = resolve_weblocs(playlist_dir)

        assert len(results) == 1
        assert results[0].name == "My Song.mka"
        assert not webloc.exists()  # consumed
        mock_tags.assert_called_once()
        tags_arg = mock_tags.call_args[0][1]
        assert tags_arg["title"] == "My Song"
        assert tags_arg["source_url"] == url

    def test_resolve_webloc_unrecognized_url_skipped(self, tmp_path):
        """A .webloc with a non-YouTube URL is left in place."""
        playlist_dir = tmp_path / "My Playlist"
        playlist_dir.mkdir()

        webloc = playlist_dir / "random.webloc"
        webloc.write_bytes(plistlib.dumps({"URL": "https://example.com/audio"}))

        mock_provider = MagicMock()
        mock_provider.can_handle.return_value = False

        with patch("yoto_lib.sources._get_providers", return_value=[mock_provider]):
            results = resolve_weblocs(playlist_dir)

        assert results == []
        assert webloc.exists()  # not consumed

    def test_resolve_webloc_download_failure_leaves_webloc(self, tmp_path):
        """If download fails, .webloc is left in place and error is not raised."""
        playlist_dir = tmp_path / "My Playlist"
        playlist_dir.mkdir()

        webloc = playlist_dir / "broken.webloc"
        webloc.write_bytes(plistlib.dumps({"URL": "https://youtu.be/bad"}))

        mock_provider = MagicMock()
        mock_provider.can_handle.return_value = True
        mock_provider.download.side_effect = RuntimeError("video unavailable")

        with patch("yoto_lib.sources._get_providers", return_value=[mock_provider]):
            results = resolve_weblocs(playlist_dir)

        assert results == []
        assert webloc.exists()

    def test_resolve_filename_collision_appends_number(self, tmp_path):
        """If the derived MKA filename already exists, append a number."""
        playlist_dir = tmp_path / "My Playlist"
        playlist_dir.mkdir()
        (playlist_dir / "My Song.mka").write_bytes(b"existing")

        url = "https://youtu.be/abc"
        webloc = playlist_dir / "dup.webloc"
        webloc.write_bytes(plistlib.dumps({"URL": url}))

        fake_audio = tmp_path / "temp.opus"
        _make_wav(fake_audio)

        mock_provider = MagicMock()
        mock_provider.can_handle.return_value = True
        mock_provider.download.return_value = (fake_audio, {"title": "My Song", "source_url": url})

        with (
            patch("yoto_lib.sources._get_providers", return_value=[mock_provider]),
            patch("yoto_lib.sources.wrap_in_mka") as mock_wrap,
            patch("yoto_lib.sources.write_tags"),
        ):
            def fake_wrap(src, dst):
                dst.write_bytes(b"fake mka")
            mock_wrap.side_effect = fake_wrap

            results = resolve_weblocs(playlist_dir)

        assert len(results) == 1
        assert results[0].name == "My Song 2.mka"
