"""Tests for yoto_lib.sync — sync engine orchestrating local → remote."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yoto_lib.sync import SyncResult, sync_playlist


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
