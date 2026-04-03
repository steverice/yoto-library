"""Tests for yoto_lib.pull — pull engine downloading remote playlists."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yoto_lib.pull import pull_playlist, PullResult


class TestPullPlaylist:
    def test_pull_creates_folder_structure(self, tmp_path, mocker):
        playlist_dir = tmp_path / "Test Playlist"
        playlist_dir.mkdir()
        (playlist_dir / ".yoto-card-id").write_text("abc12")

        remote = {
            "cardId": "abc12",
            "title": "Test Playlist",
            "content": {
                "chapters": [
                    {
                        "key": "song.mka",
                        "title": "Song",
                        "tracks": [{
                            "key": "song.mka",
                            "title": "Song",
                            "trackUrl": "https://signed.url/song.aac",
                            "duration": 120.0,
                        }],
                    },
                ],
            },
            "metadata": {
                "description": "A test playlist",
                "cover": {"imageL": "https://cover.url/img.png"},
            },
        }

        mock_api = MagicMock()
        mock_api.get_content.return_value = remote
        mocker.patch("yoto_lib.pull.YotoAPI", return_value=mock_api)

        # Mock downloads
        mocker.patch("yoto_lib.pull._download_file", return_value=b"\x00" * 100)
        mocker.patch("yoto_lib.pull._download_cover", return_value=b"\x89PNG" + b"\x00" * 50)
        mocker.patch("yoto_lib.mka.wrap_in_mka")

        result = pull_playlist(playlist_dir)

        assert result.tracks_downloaded == 1
        assert (playlist_dir / "playlist.jsonl").exists()
        assert (playlist_dir / "description.txt").exists()
        assert (playlist_dir / "description.txt").read_text() == "A test playlist"

    def test_pull_by_card_id(self, tmp_path, mocker):
        """Pull into empty folder by providing a card ID."""
        remote = {
            "cardId": "xyz99",
            "title": "New Playlist",
            "content": {"chapters": []},
            "metadata": {"description": ""},
        }

        mock_api = MagicMock()
        mock_api.get_content.return_value = remote
        mocker.patch("yoto_lib.pull.YotoAPI", return_value=mock_api)

        result = pull_playlist(tmp_path, card_id="xyz99")

        assert (tmp_path / ".yoto-card-id").read_text() == "xyz99"

    def test_pull_dry_run(self, tmp_path, mocker):
        (tmp_path / ".yoto-card-id").write_text("abc12")

        remote = {
            "cardId": "abc12",
            "title": "Test",
            "content": {"chapters": [{"key": "s.mka", "tracks": [{"trackUrl": "https://..."}]}]},
            "metadata": {"description": ""},
        }
        mock_api = MagicMock()
        mock_api.get_content.return_value = remote
        mocker.patch("yoto_lib.pull.YotoAPI", return_value=mock_api)

        result = pull_playlist(tmp_path, dry_run=True)
        assert result.dry_run is True
        assert result.tracks_downloaded == 0
