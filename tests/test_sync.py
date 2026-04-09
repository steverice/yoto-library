"""Tests for yoto_lib.sync — sync engine orchestrating local → remote."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from yoto_lib.sync import SyncResult, _has_audio_files, _parse_remote_state, sync_path, sync_playlist


def _make_audio_folder(tmp_path: Path, tracks: list[str], card_id: str | None = None) -> Path:
    """Create a minimal playlist folder with stub audio files."""
    folder = tmp_path / "My Album"
    folder.mkdir()
    for t in tracks:
        (folder / t).write_bytes(b"\x00" * 64)
    if card_id:
        (folder / ".yoto-card-id").write_text(card_id, encoding="utf-8")
    return folder


class TestSyncPlaylist:
    # ── test_sync_new_playlist ────────────────────────────────────────────────

    def test_sync_new_playlist_creates_card(self, tmp_path):
        """No card_id: creates a new card and writes .yoto-card-id."""
        folder = _make_audio_folder(tmp_path, ["track01.mp3"])

        mock_api = MagicMock()
        mock_api.get_content.side_effect = Exception("no remote card yet")
        mock_api.upload_and_transcode.return_value = {"transcodedSha256": "abc123"}
        mock_api.create_or_update_content.return_value = {"cardId": "NEW-001"}

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
        ):
            result = sync_playlist(folder)

        assert result.card_id == "NEW-001"
        assert result.tracks_uploaded == 1

        card_id_file = folder / ".yoto-card-id"
        assert card_id_file.exists()
        assert card_id_file.read_text(encoding="utf-8") == "NEW-001"

    # ── test_sync_existing_playlist_no_changes ────────────────────────────────

    def test_sync_existing_playlist_no_changes(self, tmp_path):
        """Existing card_id with no new tracks: zero uploads, no errors."""
        folder = _make_audio_folder(tmp_path, ["track01.mp3"], card_id="EXISTING-001")

        remote_content = {
            "content": {
                "chapters": {
                    "track01.mp3": {
                        "title": "track01",
                        "tracks": [{"trackUrl": "yoto:#deadbeef"}],
                    }
                }
            }
        }

        mock_api = MagicMock()
        mock_api.get_content.return_value = remote_content
        mock_api.create_or_update_content.return_value = {"cardId": "EXISTING-001"}

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
        ):
            result = sync_playlist(folder)

        assert result.card_id == "EXISTING-001"
        assert result.tracks_uploaded == 0
        assert result.cover_uploaded is False
        assert result.errors == []
        mock_api.upload_and_transcode.assert_not_called()

    # ── test_sync_dry_run_does_not_upload ─────────────────────────────────────

    def test_sync_dry_run_does_not_upload(self, tmp_path):
        """dry_run=True: returns counts without calling any upload methods."""
        folder = _make_audio_folder(tmp_path, ["track01.mp3", "track02.mp3"])

        mock_api = MagicMock()
        mock_api.get_content.return_value = {}

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
        ):
            result = sync_playlist(folder, dry_run=True)

        assert result.dry_run is True
        assert result.tracks_uploaded == 2
        mock_api.upload_and_transcode.assert_not_called()
        mock_api.upload_cover.assert_not_called()
        mock_api.create_or_update_content.assert_not_called()

    # ── test_sync_uploads_cover ───────────────────────────────────────────────

    def test_sync_uploads_cover(self, tmp_path):
        """When cover.png is present and remote has no cover, upload it."""
        folder = _make_audio_folder(tmp_path, ["track01.mp3"], card_id="CARD-COVER")
        (folder / "cover.png").write_bytes(b"\x89PNG\r\n")

        remote_content = {
            "content": {
                "chapters": {
                    "track01.mp3": {
                        "title": "track01",
                        "tracks": [{"trackUrl": "yoto:#feedcafe"}],
                    }
                }
            }
        }

        mock_api = MagicMock()
        mock_api.get_content.return_value = remote_content
        mock_api.upload_cover.return_value = {"url": "https://cdn.yoto.io/cover.png"}
        mock_api.create_or_update_content.return_value = {"cardId": "CARD-COVER"}

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
        ):
            result = sync_playlist(folder)

        assert result.cover_uploaded is True
        mock_api.upload_cover.assert_called_once_with(folder / "cover.png")


class TestSyncWithWeblocs:
    def test_sync_resolves_weblocs_before_scanning(self, tmp_path):
        """sync_playlist calls resolve_weblocs before loading the playlist."""
        folder = _make_audio_folder(tmp_path, ["track01.mp3"])

        mock_api = MagicMock()
        mock_api.upload_and_transcode.return_value = {"transcodedSha256": "abc"}
        mock_api.create_or_update_content.return_value = {"cardId": "NEW-002"}

        resolve_called = [False]

        def fake_resolve(d, trim=True):
            resolve_called[0] = True
            return []

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
            patch("yoto_lib.sync.resolve_weblocs", side_effect=fake_resolve) as mock_resolve,
        ):
            result = sync_playlist(folder)

        mock_resolve.assert_called_once_with(folder, trim=True)
        assert resolve_called[0]


# ── TestParseRemoteState ─────────────────────────────────────────────────────


class TestParseRemoteState:
    def test_parses_dict_chapters(self):
        """Chapters as dict (our upload format) extracts tracks and hashes."""
        remote = {
            "content": {
                "chapters": {
                    "track.mp3": {
                        "title": "My Track",
                        "tracks": [{"trackUrl": "yoto:#abc123"}],
                    }
                }
            }
        }
        state = _parse_remote_state(remote)
        assert state["tracks"] == ["My Track"]
        assert state["track_hashes"] == {"My Track": "abc123"}

    def test_parses_list_chapters(self):
        """Chapters as list (API format) extracts tracks and hashes."""
        remote = {
            "content": {
                "chapters": [
                    {
                        "key": "ch1",
                        "title": "Track One",
                        "tracks": [{"trackUrl": "yoto:#def456"}],
                    },
                    {
                        "key": "ch2",
                        "title": "Track Two",
                        "tracks": [{"trackUrl": "yoto:#ghi789"}],
                    },
                ]
            }
        }
        state = _parse_remote_state(remote)
        assert state["tracks"] == ["Track One", "Track Two"]
        assert state["track_hashes"]["Track One"] == "def456"
        assert state["track_hashes"]["Track Two"] == "ghi789"

    def test_parses_cover_url(self):
        """Extracts cover URL and has_cover flag from metadata."""
        remote = {
            "content": {"chapters": {}},
            "metadata": {"cover": {"imageL": "https://cdn.yoto.io/cover.png"}},
        }
        state = _parse_remote_state(remote)
        assert state["has_cover"] is True
        assert state["cover_url"] == "https://cdn.yoto.io/cover.png"

    def test_missing_metadata(self):
        """No metadata key → has_cover=False, cover_url=None."""
        remote = {"content": {"chapters": {}}}
        state = _parse_remote_state(remote)
        assert state["has_cover"] is False
        assert state["cover_url"] is None

    def test_parses_track_info(self):
        """Extracts format and channels from track entries."""
        remote = {
            "content": {
                "chapters": [
                    {
                        "key": "ch1",
                        "title": "Song",
                        "tracks": [
                            {
                                "trackUrl": "yoto:#abc",
                                "format": "opus",
                                "channels": "stereo",
                            }
                        ],
                    }
                ]
            }
        }
        state = _parse_remote_state(remote)
        assert state["track_info"]["Song"] == {"format": "opus", "channels": "stereo"}

    def test_empty_content(self):
        """Empty content dict → empty tracks list."""
        state = _parse_remote_state({"content": {}})
        assert state["tracks"] == []
        assert state["track_hashes"] == {}


# ── TestHasAudioFiles ────────────────────────────────────────────────────────


class TestHasAudioFiles:
    def test_folder_with_mp3(self, tmp_path):
        (tmp_path / "song.mp3").write_bytes(b"\x00")
        assert _has_audio_files(tmp_path) is True

    def test_folder_with_mka(self, tmp_path):
        (tmp_path / "track.mka").write_bytes(b"\x00")
        assert _has_audio_files(tmp_path) is True

    def test_folder_with_webloc(self, tmp_path):
        (tmp_path / "link.webloc").write_bytes(b"\x00")
        assert _has_audio_files(tmp_path) is True

    def test_empty_folder(self, tmp_path):
        assert _has_audio_files(tmp_path) is False

    def test_folder_with_non_audio(self, tmp_path):
        (tmp_path / "readme.txt").write_bytes(b"\x00")
        assert _has_audio_files(tmp_path) is False

    def test_folder_with_png(self, tmp_path):
        (tmp_path / "cover.png").write_bytes(b"\x00")
        assert _has_audio_files(tmp_path) is False


# ── TestSyncPath ─────────────────────────────────────────────────────────────


class TestSyncPath:
    def test_single_playlist_folder(self, tmp_path):
        """Folder with audio files → syncs as single playlist."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track.mp3").write_bytes(b"\x00" * 64)

        fake_result = SyncResult(card_id="CARD01", tracks_uploaded=1)

        with patch("yoto_lib.sync.sync_playlist", return_value=fake_result) as mock_sync:
            results = sync_path(folder)

        assert len(results) == 1
        mock_sync.assert_called_once()

    def test_multiple_subdirs(self, tmp_path):
        """Parent dir with subdirs containing audio → syncs each."""
        for name in ("Album A", "Album B"):
            sub = tmp_path / name
            sub.mkdir()
            (sub / "track.mp3").write_bytes(b"\x00" * 64)

        fake_result = SyncResult(card_id="X", tracks_uploaded=1)

        with patch("yoto_lib.sync.sync_playlist", return_value=fake_result) as mock_sync:
            results = sync_path(tmp_path)

        assert len(results) == 2
        assert mock_sync.call_count == 2

    def test_no_audio_anywhere(self, tmp_path):
        """Parent dir with no audio files anywhere → empty results."""
        (tmp_path / "readme.txt").write_bytes(b"\x00")

        with patch("yoto_lib.sync.sync_playlist") as mock_sync:
            results = sync_path(tmp_path)

        assert results == []
        mock_sync.assert_not_called()


# ── TestSyncErrorPaths ───────────────────────────────────────────────────────


class TestSyncErrorPaths:
    def test_upload_failure_records_error(self, tmp_path):
        """When upload_and_transcode fails, error is recorded but sync continues."""
        folder = _make_audio_folder(tmp_path, ["track01.mp3", "track02.mp3"])

        mock_api = MagicMock()
        mock_api.get_content.return_value = {}
        # First upload fails, second succeeds
        mock_api.upload_and_transcode.side_effect = [
            OSError("timeout"),
            {"transcodedSha256": "abc"},
        ]
        mock_api.create_or_update_content.return_value = {"cardId": "NEW-ERR"}

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
        ):
            result = sync_playlist(folder)

        assert len(result.errors) >= 1
        assert any("timeout" in e for e in result.errors)

    def test_content_post_failure(self, tmp_path):
        """When create_or_update_content fails, error is recorded."""
        folder = _make_audio_folder(tmp_path, ["track01.mp3"])

        mock_api = MagicMock()
        mock_api.get_content.return_value = {}
        mock_api.upload_and_transcode.return_value = {"transcodedSha256": "abc"}
        mock_api.create_or_update_content.side_effect = OSError("API 500")

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
        ):
            result = sync_playlist(folder)

        assert any("Content POST failed" in e for e in result.errors)

    def test_missing_track_file_records_error(self, tmp_path):
        """When a track file doesn't exist on disk, error is recorded."""
        folder = tmp_path / "My Album"
        folder.mkdir()

        # Create a mock playlist that references a non-existent file
        mock_playlist = MagicMock()
        mock_playlist.card_id = None
        mock_playlist.card_id_path = folder / ".yoto-card-id"
        mock_playlist.title = "My Album"
        mock_playlist.track_files = ["ghost.mp3"]
        mock_playlist.has_cover = False
        mock_playlist.cover_path = folder / "cover.png"
        mock_playlist.description = None
        mock_playlist.description_path = folder / "description.txt"
        mock_playlist.path = folder

        mock_api = MagicMock()
        mock_api.get_content.return_value = {}
        mock_api.create_or_update_content.return_value = {"cardId": "NEW-GHOST"}

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.load_playlist", return_value=mock_playlist),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.generate_description"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
            patch("yoto_lib.sync.diff_playlists") as mock_diff,
        ):
            mock_diff.return_value = MagicMock(
                new_tracks=["ghost.mp3"],
                removed_tracks=[],
                order_changed=False,
                cover_changed=False,
                metadata_changed=False,
            )
            result = sync_playlist(folder)

        # ghost.mp3 should cause a "not found" error since it doesn't exist on disk
        assert any("not found" in e.lower() or "ghost.mp3" in e for e in result.errors)

    def test_preserves_remote_cover_url(self, tmp_path):
        """When cover hasn't changed, remote cover URL is preserved (not re-uploaded)."""
        folder = _make_audio_folder(tmp_path, ["track01.mp3"], card_id="COVER-KEEP")
        # No local cover.png — so cover_changed is False
        remote_content = {
            "content": {
                "chapters": {
                    "track01.mp3": {
                        "title": "track01",
                        "tracks": [{"trackUrl": "yoto:#abc"}],
                    }
                }
            },
            "metadata": {"cover": {"imageL": "https://cdn.yoto.io/existing.png"}},
        }

        mock_api = MagicMock()
        mock_api.get_content.return_value = remote_content
        mock_api.create_or_update_content.return_value = {"cardId": "COVER-KEEP"}

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.generate_cover_if_missing"),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
            patch("yoto_lib.sync.build_content_schema") as mock_schema,
        ):
            mock_schema.return_value = {}
            result = sync_playlist(folder)

        mock_api.upload_cover.assert_not_called()
        # Verify the existing cover URL was passed to build_content_schema
        call_args = mock_schema.call_args
        cover_url_arg = call_args.args[3] if len(call_args.args) > 3 else call_args.kwargs.get("cover_url")
        assert cover_url_arg == "https://cdn.yoto.io/existing.png"


# ── TestSyncResultFolder ─────────────────────────────────────────────────────


class TestSyncResultFolder:
    def test_sync_result_has_folder(self):
        """SyncResult carries the folder path for post-sync actions like printing."""
        from yoto_lib.sync import SyncResult

        result = SyncResult(folder=Path("/tmp/album"))
        assert result.folder == Path("/tmp/album")

    def test_sync_result_folder_default_none(self):
        """SyncResult.folder defaults to None."""
        from yoto_lib.sync import SyncResult

        result = SyncResult()
        assert result.folder is None
