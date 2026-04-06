"""Tests for yoto_lib.pull — pull engine downloading remote playlists."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from yoto_lib.pull import pull_playlist, PullResult, _sanitize_filename, _process_track, _TrackJob


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
        mocker.patch("yoto_lib.pull.wrap_in_mka")

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


# ── TestSanitizeFilename ─────────────────────────────────────────────────────


class TestSanitizeFilename:
    def test_replaces_slashes(self):
        assert _sanitize_filename("path/to/file") == "path-to-file"

    def test_replaces_colons(self):
        assert _sanitize_filename("time: 3:00") == "time- 3-00"

    def test_removes_null_bytes(self):
        assert _sanitize_filename("file\x00name") == "filename"

    def test_strips_whitespace(self):
        assert _sanitize_filename("  song  ") == "song"

    def test_clean_name_unchanged(self):
        assert _sanitize_filename("Normal Song Name") == "Normal Song Name"

    def test_combined(self):
        assert _sanitize_filename("  a/b:c\x00d  ") == "a-b-cd"


# ── TestProcessTrack ─────────────────────────────────────────────────────────


class TestProcessTrack:
    def test_successful_download_and_icon(self, tmp_path, mocker):
        """Both download and icon succeed → (True, True, None)."""
        mocker.patch("yoto_lib.pull._download_file", return_value=b"\x00" * 100)
        mocker.patch("yoto_lib.pull.wrap_in_mka")
        mocker.patch("yoto_lib.pull.download_icon", return_value=b"\x89PNG")
        mocker.patch("yoto_lib.pull.apply_icon_to_mka")

        job = _TrackJob(title="Song", filename="Song.mka", track_url="https://ex.com/song", icon_ref="icon123")
        track_ok, icon_ok, error = _process_track(job, tmp_path, tmp_path / "cache")

        assert track_ok is True
        assert icon_ok is True
        assert error is None

    def test_download_failure(self, tmp_path, mocker):
        """Download raises → (False, False, error string)."""
        mocker.patch("yoto_lib.pull._download_file", side_effect=Exception("connection refused"))
        mocker.patch("yoto_lib.pull.wrap_in_mka")

        job = _TrackJob(title="Song", filename="Song.mka", track_url="https://ex.com/song", icon_ref="")
        track_ok, icon_ok, error = _process_track(job, tmp_path, tmp_path / "cache")

        assert track_ok is False
        assert icon_ok is False
        assert "connection refused" in error

    def test_download_ok_icon_fails(self, tmp_path, mocker):
        """Download succeeds but icon fails → (True, False, error string)."""
        mocker.patch("yoto_lib.pull._download_file", return_value=b"\x00" * 100)
        mocker.patch("yoto_lib.pull.wrap_in_mka")
        mocker.patch("yoto_lib.pull.download_icon", side_effect=Exception("icon 404"))
        mocker.patch("yoto_lib.pull.apply_icon_to_mka")

        job = _TrackJob(title="Song", filename="Song.mka", track_url="https://ex.com/song", icon_ref="icon123")
        track_ok, icon_ok, error = _process_track(job, tmp_path, tmp_path / "cache")

        assert track_ok is True
        assert icon_ok is False
        assert "icon" in error.lower()

    def test_no_icon_ref_skips_icon(self, tmp_path, mocker):
        """Empty icon_ref → track downloads but no icon attempt."""
        mocker.patch("yoto_lib.pull._download_file", return_value=b"\x00" * 100)
        mocker.patch("yoto_lib.pull.wrap_in_mka")
        mock_dl_icon = mocker.patch("yoto_lib.pull.download_icon")

        job = _TrackJob(title="Song", filename="Song.mka", track_url="https://ex.com/song", icon_ref="")
        track_ok, icon_ok, error = _process_track(job, tmp_path, tmp_path / "cache")

        assert track_ok is True
        assert icon_ok is False
        assert error is None
        mock_dl_icon.assert_not_called()


# ── TestPullNoCardId ─────────────────────────────────────────────────────────


class TestPullNoCardId:
    def test_no_card_id_returns_error(self, tmp_path):
        """No card_id and no .yoto-card-id file → error in result."""
        result = pull_playlist(tmp_path)
        assert len(result.errors) >= 1
        assert any("No card ID" in e for e in result.errors)
        assert result.card_id is None


# ── TestPullCover ────────────────────────────────────────────────────────────


class TestPullCover:
    def test_downloads_cover(self, tmp_path, mocker):
        """Pull downloads cover.png when metadata has cover URL."""
        (tmp_path / ".yoto-card-id").write_text("abc12")

        remote = {
            "cardId": "abc12",
            "title": "Test",
            "content": {"chapters": []},
            "metadata": {
                "description": "",
                "cover": {"imageL": "https://cdn.yoto.io/cover.png"},
            },
        }
        mock_api = MagicMock()
        mock_api.get_content.return_value = remote
        mocker.patch("yoto_lib.pull.YotoAPI", return_value=mock_api)
        mocker.patch("yoto_lib.pull._download_file", return_value=b"\x89PNG cover data")

        result = pull_playlist(tmp_path)

        assert result.cover_downloaded is True
        assert (tmp_path / "cover.png").exists()
        assert (tmp_path / "cover.png").read_bytes() == b"\x89PNG cover data"

    def test_cover_failure_records_error(self, tmp_path, mocker):
        """Cover download failure records error but doesn't stop pull."""
        (tmp_path / ".yoto-card-id").write_text("abc12")

        remote = {
            "cardId": "abc12",
            "title": "Test",
            "content": {"chapters": []},
            "metadata": {
                "description": "",
                "cover": {"imageL": "https://cdn.yoto.io/cover.png"},
            },
        }
        mock_api = MagicMock()
        mock_api.get_content.return_value = remote
        mocker.patch("yoto_lib.pull.YotoAPI", return_value=mock_api)
        mocker.patch("yoto_lib.pull._download_file", side_effect=Exception("CDN down"))

        result = pull_playlist(tmp_path)

        assert result.cover_downloaded is False
        assert any("cover" in e.lower() for e in result.errors)


# ── TestPullCallback ─────────────────────────────────────────────────────────


class TestPullCallback:
    def test_on_track_done_called(self, tmp_path, mocker):
        """on_track_done callback is called for each downloaded track."""
        (tmp_path / ".yoto-card-id").write_text("abc12")

        remote = {
            "cardId": "abc12",
            "title": "Test",
            "content": {
                "chapters": [
                    {
                        "key": "s1.mka",
                        "title": "Song 1",
                        "tracks": [{"trackUrl": "https://ex.com/s1.aac", "title": "Song 1"}],
                    },
                    {
                        "key": "s2.mka",
                        "title": "Song 2",
                        "tracks": [{"trackUrl": "https://ex.com/s2.aac", "title": "Song 2"}],
                    },
                ]
            },
            "metadata": {"description": ""},
        }
        mock_api = MagicMock()
        mock_api.get_content.return_value = remote
        mocker.patch("yoto_lib.pull.YotoAPI", return_value=mock_api)
        mocker.patch("yoto_lib.pull._download_file", return_value=b"\x00" * 50)
        mocker.patch("yoto_lib.pull.wrap_in_mka")

        titles_done = []
        result = pull_playlist(tmp_path, on_track_done=titles_done.append)

        assert result.tracks_downloaded == 2
        assert len(titles_done) == 2

    def test_writes_playlist_in_chapter_order(self, tmp_path, mocker):
        """playlist.jsonl preserves chapter order from remote."""
        (tmp_path / ".yoto-card-id").write_text("abc12")

        remote = {
            "cardId": "abc12",
            "title": "Test",
            "content": {
                "chapters": [
                    {"key": "ch1", "title": "Zebra", "tracks": [{"trackUrl": "https://ex.com/z.aac", "title": "Zebra"}]},
                    {"key": "ch2", "title": "Apple", "tracks": [{"trackUrl": "https://ex.com/a.aac", "title": "Apple"}]},
                    {"key": "ch3", "title": "Mango", "tracks": [{"trackUrl": "https://ex.com/m.aac", "title": "Mango"}]},
                ]
            },
            "metadata": {"description": ""},
        }
        mock_api = MagicMock()
        mock_api.get_content.return_value = remote
        mocker.patch("yoto_lib.pull.YotoAPI", return_value=mock_api)
        mocker.patch("yoto_lib.pull._download_file", return_value=b"\x00" * 50)
        mocker.patch("yoto_lib.pull.wrap_in_mka")

        result = pull_playlist(tmp_path)

        import json
        jsonl = (tmp_path / "playlist.jsonl").read_text(encoding="utf-8")
        filenames = [json.loads(line) for line in jsonl.splitlines() if line.strip()]
        assert filenames == ["Zebra.mka", "Apple.mka", "Mango.mka"]
