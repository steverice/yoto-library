"""Tests for auto-generated playlist descriptions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yoto_lib.description import generate_description, _collect_metadata, _build_prompt


class TestGenerateDescription:
    def test_skips_when_description_exists(self, tmp_path):
        """generate_description does nothing when description.txt already exists."""
        desc_path = tmp_path / "description.txt"
        desc_path.write_text("Existing description", encoding="utf-8")

        playlist = MagicMock()
        playlist.description_path = desc_path
        playlist.description = "Existing description"

        with patch("yoto_lib.description.subprocess") as mock_sub:
            generate_description(playlist)

        mock_sub.run.assert_not_called()


class TestCollectMetadata:
    def test_collects_titles_and_artists(self, tmp_path):
        """Collects track titles and deduplicates artists."""
        playlist = MagicMock()
        playlist.path = tmp_path
        playlist.track_files = ["track1.mka", "track2.mka"]

        with patch("yoto_lib.description.mka.read_tags") as mock_read:
            mock_read.side_effect = [
                {"title": "Song One", "artist": "Artist A", "genre": "Pop"},
                {"title": "Song Two", "artist": "Artist A", "genre": "Pop"},
            ]
            result = _collect_metadata(playlist)

        assert result["track_titles"] == ["Song One", "Song Two"]
        assert result["artist"] == ["Artist A"]  # deduplicated
        assert result["genre"] == ["Pop"]  # deduplicated

    def test_falls_back_to_filename_stem(self, tmp_path):
        """Uses filename stem when title tag is missing."""
        playlist = MagicMock()
        playlist.path = tmp_path
        playlist.track_files = ["my-song.mka"]

        with patch("yoto_lib.description.mka.read_tags") as mock_read:
            mock_read.return_value = {}
            result = _collect_metadata(playlist)

        assert result["track_titles"] == ["my-song"]

    def test_handles_read_failure(self, tmp_path):
        """Continues gracefully when read_tags raises."""
        playlist = MagicMock()
        playlist.path = tmp_path
        playlist.track_files = ["broken.mka"]

        with patch("yoto_lib.description.mka.read_tags") as mock_read:
            mock_read.side_effect = Exception("ffprobe failed")
            result = _collect_metadata(playlist)

        assert result["track_titles"] == ["broken"]


class TestBuildPrompt:
    def test_includes_playlist_title(self):
        metadata = {"track_titles": [], "artist": [], "genre": [],
                    "album_artist": [], "composer": [], "read_by": [],
                    "category": [], "min_age": [], "max_age": []}
        prompt = _build_prompt("My Playlist", metadata)
        assert "Playlist: My Playlist" in prompt

    def test_includes_tracks_and_artist(self):
        metadata = {"track_titles": ["Song A", "Song B"], "artist": ["Bob"],
                    "genre": ["Rock"], "album_artist": [], "composer": [],
                    "read_by": [], "category": [], "min_age": [], "max_age": []}
        prompt = _build_prompt("Test", metadata)
        assert "- Song A" in prompt
        assert "- Song B" in prompt
        assert "Artist: Bob" in prompt
        assert "Genre: Rock" in prompt

    def test_omits_empty_fields(self):
        metadata = {"track_titles": ["Song"], "artist": [], "genre": [],
                    "album_artist": [], "composer": [], "read_by": [],
                    "category": [], "min_age": [], "max_age": []}
        prompt = _build_prompt("Test", metadata)
        assert "Artist:" not in prompt
        assert "Genre:" not in prompt
        assert "Composer:" not in prompt


class TestGenerateDescriptionIntegration:
    def test_generates_and_writes_description(self, tmp_path):
        """Full flow: reads tags, calls claude, writes description.txt."""
        playlist = MagicMock()
        playlist.path = tmp_path
        playlist.title = "Daniel Tiger Songs"
        playlist.description_path = tmp_path / "description.txt"
        playlist.description = None
        playlist.track_files = ["song1.mka", "song2.mka"]

        with patch("yoto_lib.description.mka.read_tags") as mock_tags, \
             patch("yoto_lib.description.subprocess.run") as mock_run:
            mock_tags.side_effect = [
                {"title": "Beautiful Day", "artist": "Daniel Tiger"},
                {"title": "Use Your Words", "artist": "Daniel Tiger"},
            ]
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"result": "A friendly tiger explores feelings and friendship in his neighborhood."}',
            )

            log_messages = []
            generate_description(playlist, log=log_messages.append)

        assert playlist.description_path.exists()
        content = playlist.description_path.read_text(encoding="utf-8")
        assert "friendly tiger" in content
        assert any("Generated description:" in m for m in log_messages)

    def test_continues_on_claude_failure(self, tmp_path):
        """When claude CLI fails, logs warning and does not write file."""
        playlist = MagicMock()
        playlist.path = tmp_path
        playlist.title = "Test"
        playlist.description_path = tmp_path / "description.txt"
        playlist.description = None
        playlist.track_files = ["song.mka"]

        with patch("yoto_lib.description.mka.read_tags") as mock_tags, \
             patch("yoto_lib.description.subprocess.run") as mock_run:
            mock_tags.return_value = {"title": "Song"}
            mock_run.side_effect = FileNotFoundError("claude not found")

            log_messages = []
            generate_description(playlist, log=log_messages.append)

        assert not playlist.description_path.exists()
        assert any("Warning" in m for m in log_messages)
