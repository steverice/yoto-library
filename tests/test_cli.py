"""Tests for yoto_cli.main — CLI layer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from yoto_cli.main import cli
from yoto_lib.auth import AuthError
from yoto_lib.sync import SyncResult


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def runner():
    return CliRunner()


# ── test_auth_command_runs ────────────────────────────────────────────────────


class TestAuthCommand:
    def test_auth_command_runs(self, runner):
        """auth calls run_device_code_flow and exits 0 on success."""
        with patch("yoto_cli.main.run_device_code_flow") as mock_flow:
            result = runner.invoke(cli, ["auth"])
        mock_flow.assert_called_once()
        assert result.exit_code == 0

    def test_auth_converts_auth_error_to_click_exception(self, runner):
        """auth catches AuthError and re-raises as ClickException (exit 1)."""
        with patch(
            "yoto_cli.main.run_device_code_flow",
            side_effect=AuthError("Device code expired"),
        ):
            result = runner.invoke(cli, ["auth"])
        assert result.exit_code != 0
        assert "Device code expired" in result.output


# ── test_list_shows_cards ─────────────────────────────────────────────────────


class TestListCommand:
    def test_list_shows_cards(self, runner):
        """list calls get_my_content and prints a table of card IDs and titles."""
        fake_cards = [
            {
                "cardId": "CARD001",
                "title": "My Album",
                "content": {"chapters": {"t1.mka": {}, "t2.mka": {}}},
            },
            {
                "cardId": "CARD002",
                "title": "Kids Stories",
                "content": {"chapters": {}},
            },
        ]
        mock_api = MagicMock()
        mock_api.get_my_content.return_value = fake_cards

        with patch("yoto_cli.main.YotoAPI", return_value=mock_api):
            result = runner.invoke(cli, ["list"])

        assert result.exit_code == 0
        assert "CARD001" in result.output
        assert "My Album" in result.output
        assert "CARD002" in result.output
        assert "Kids Stories" in result.output

    def test_list_empty_account(self, runner):
        """list prints a message when no cards are found."""
        mock_api = MagicMock()
        mock_api.get_my_content.return_value = []

        with patch("yoto_cli.main.YotoAPI", return_value=mock_api):
            result = runner.invoke(cli, ["list"])

        assert result.exit_code == 0
        assert "No cards found" in result.output


# ── test_sync_runs ────────────────────────────────────────────────────────────


class TestSyncCommand:
    def test_sync_runs(self, runner, tmp_path):
        """sync calls sync_path and prints card + track count."""
        # Create a minimal folder so the path argument is valid
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id="SYNCED-001", tracks_uploaded=3)]

        with patch("yoto_cli.main.sync_path", return_value=fake_results) as mock_sync:
            result = runner.invoke(cli, ["sync", str(folder)])

        assert result.exit_code == 0
        mock_sync.assert_called_once_with(folder, dry_run=False, log=mock_sync.call_args.kwargs["log"])
        assert "SYNCED-001" in result.output
        assert "3 tracks" in result.output

    def test_sync_dry_run(self, runner, tmp_path):
        """sync --dry-run prints dry-run message without uploading."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id=None, tracks_uploaded=2, dry_run=True)]

        with patch("yoto_cli.main.sync_path", return_value=fake_results) as mock_sync:
            result = runner.invoke(cli, ["sync", "--dry-run", str(folder)])

        assert result.exit_code == 0
        mock_sync.assert_called_once_with(folder, dry_run=True)
        assert "[Dry run]" in result.output
        assert "2 tracks" in result.output


# ── test_reorder_opens_editor ─────────────────────────────────────────────────


class TestReorderCommand:
    def test_reorder_opens_editor(self, runner, tmp_path):
        """reorder opens $EDITOR; saves reordered content back to the file."""
        playlist_path = tmp_path / "playlist.jsonl"
        original_lines = ['"track_a.mka"', '"track_b.mka"', '"track_c.mka"']
        playlist_path.write_text("\n".join(original_lines) + "\n", encoding="utf-8")

        # Simulate the user swapping b and c
        edited_content = '"track_a.mka"\n"track_c.mka"\n"track_b.mka"\n'

        with patch("yoto_cli.main.click.edit", return_value=edited_content) as mock_edit:
            result = runner.invoke(cli, ["reorder", str(playlist_path)])

        assert result.exit_code == 0, result.output
        mock_edit.assert_called_once()

        saved = playlist_path.read_text(encoding="utf-8")
        saved_names = [json.loads(line) for line in saved.splitlines() if line.strip()]
        assert saved_names == ["track_a.mka", "track_c.mka", "track_b.mka"]

    def test_reorder_no_changes(self, runner, tmp_path):
        """reorder prints 'No changes' when editor returns None."""
        playlist_path = tmp_path / "playlist.jsonl"
        playlist_path.write_text('"track_a.mka"\n', encoding="utf-8")

        with patch("yoto_cli.main.click.edit", return_value=None):
            result = runner.invoke(cli, ["reorder", str(playlist_path)])

        assert result.exit_code == 0
        assert "No changes" in result.output


# ── test_init_creates_folder ──────────────────────────────────────────────────


class TestInitCommand:
    def test_init_creates_folder(self, runner, tmp_path):
        """init creates the folder and writes an empty playlist.jsonl."""
        new_folder = tmp_path / "my-new-playlist"
        assert not new_folder.exists()

        result = runner.invoke(cli, ["init", str(new_folder)])

        assert result.exit_code == 0, result.output
        assert new_folder.exists()
        jsonl_path = new_folder / "playlist.jsonl"
        assert jsonl_path.exists()
        # Empty playlist.jsonl should be empty (no filenames)
        content = jsonl_path.read_text(encoding="utf-8")
        filenames = [json.loads(ln) for ln in content.splitlines() if ln.strip()]
        assert filenames == []

    def test_init_existing_folder(self, runner, tmp_path):
        """init on an existing folder with playlist.jsonl reports already-exists."""
        folder = tmp_path / "existing"
        folder.mkdir()
        jsonl = folder / "playlist.jsonl"
        jsonl.write_text('"track.mka"\n', encoding="utf-8")

        result = runner.invoke(cli, ["init", str(folder)])

        assert result.exit_code == 0
        assert "Already exists" in result.output
        # Original content must be preserved
        assert json.loads(jsonl.read_text().strip()) == "track.mka"
