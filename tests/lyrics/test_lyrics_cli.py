"""Tests for the yoto lyrics CLI command."""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from yoto_cli.main import cli


def ffmpeg_available():
    import subprocess

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _make_wav(path: Path) -> Path:
    sample_rate = 44100
    data_size = 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    path.write_bytes(header + b"\x00\x00")
    return path


@needs_ffmpeg
class TestLyricsCommand:
    def test_fetches_and_writes_lyrics(self, tmp_path):
        from yoto_lib.mka import wrap_in_mka, write_tags

        mka = tmp_path / "track.mka"
        wav = tmp_path / "silence.wav"
        _make_wav(wav)
        wrap_in_mka(wav, mka)
        write_tags(mka, {"title": "Old MacDonald", "artist": "Kids Songs"})

        with patch("yoto_cli.commands.lyrics.get_lyrics", return_value=("E-I-E-I-O", "lrclib")) as mock_get:
            runner = CliRunner()
            result = runner.invoke(cli, ["lyrics", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "lrclib" in result.output.lower() or "lyrics" in result.output.lower()

    def test_skips_tracks_with_existing_lyrics(self, tmp_path):
        from yoto_lib.mka import wrap_in_mka, write_tags

        mka = tmp_path / "track.mka"
        wav = tmp_path / "silence.wav"
        _make_wav(wav)
        wrap_in_mka(wav, mka)
        write_tags(mka, {"title": "Song", "artist": "Artist", "lyrics": "Existing"})

        with patch("yoto_cli.commands.lyrics.get_lyrics") as mock_get:
            runner = CliRunner()
            result = runner.invoke(cli, ["lyrics", str(tmp_path)])

        assert result.exit_code == 0, result.output
        mock_get.assert_not_called()

    def test_force_refetches_existing_lyrics(self, tmp_path):
        from yoto_lib.mka import wrap_in_mka, write_tags

        mka = tmp_path / "track.mka"
        wav = tmp_path / "silence.wav"
        _make_wav(wav)
        wrap_in_mka(wav, mka)
        write_tags(mka, {"title": "Song", "artist": "Artist", "lyrics": "Old lyrics"})

        with patch("yoto_cli.commands.lyrics.get_lyrics", return_value=("New lyrics", "lrclib")):
            runner = CliRunner()
            result = runner.invoke(cli, ["lyrics", "--force", str(tmp_path)])

        assert result.exit_code == 0, result.output

    def test_handles_no_mka_files(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["lyrics", str(tmp_path)])

        assert result.exit_code == 0, result.output


class TestSelectIconLyricsSummary:
    @needs_ffmpeg
    def test_select_icon_generates_and_caches_summary(self, tmp_path):
        """Verify select-icon reads LYRICS, generates summary, and writes YOTO_LYRICS_SUMMARY."""
        from yoto_lib.mka import wrap_in_mka, write_tags

        mka = tmp_path / "track.mka"
        wav = tmp_path / "silence.wav"
        _make_wav(wav)
        wrap_in_mka(wav, mka)
        write_tags(mka, {"title": "Old MacDonald", "lyrics": "Had a farm E-I-E-I-O"})

        with (
            patch(
                "yoto_lib.icons.icon_llm.describe_icons_llm", return_value=["cow", "barn", "tractor"]
            ) as mock_describe,
            patch(
                "yoto_lib.icons.icon_llm.summarize_lyrics_for_icon", return_value="A farm with cows and a red barn"
            ) as mock_summarize,
            patch("yoto_lib.icons.icon_llm.match_icon_llm", return_value=(None, 0.0)),
            patch("yoto_lib.icons.generate_retrodiffusion_icons", return_value=None),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["select-icon", str(mka)])

        # Summary should have been generated
        mock_summarize.assert_called_once()
        # And passed to describe_icons_llm
        _, kwargs = mock_describe.call_args
        assert kwargs.get("lyrics_summary") == "A farm with cows and a red barn"

    @needs_ffmpeg
    def test_select_icon_uses_cached_summary(self, tmp_path):
        """Verify select-icon uses existing YOTO_LYRICS_SUMMARY without regenerating."""
        from yoto_lib.mka import wrap_in_mka, write_tags

        mka = tmp_path / "track.mka"
        wav = tmp_path / "silence.wav"
        _make_wav(wav)
        wrap_in_mka(wav, mka)
        write_tags(
            mka,
            {
                "title": "Old MacDonald",
                "lyrics": "Had a farm",
                "lyrics_summary": "Cached: farm with animals",
            },
        )

        with (
            patch(
                "yoto_lib.icons.icon_llm.describe_icons_llm", return_value=["cow", "barn", "tractor"]
            ) as mock_describe,
            patch("yoto_lib.icons.icon_llm.summarize_lyrics_for_icon") as mock_summarize,
            patch("yoto_lib.icons.icon_llm.match_icon_llm", return_value=(None, 0.0)),
            patch("yoto_lib.icons.generate_retrodiffusion_icons", return_value=None),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["select-icon", str(mka)])

        # Should NOT have regenerated summary
        mock_summarize.assert_not_called()
        # Should have used the cached one
        _, kwargs = mock_describe.call_args
        assert kwargs.get("lyrics_summary") == "Cached: farm with animals"
