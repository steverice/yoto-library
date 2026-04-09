"""Tests for yoto_cli — CLI layer."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from yoto_cli.main import _is_card_id, _strip_track_number, cli
from yoto_lib.covers.printer import PrintError
from yoto_lib.pull import PullResult
from yoto_lib.sync import SyncResult
from yoto_lib.yoto.auth import AuthError

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def runner():
    return CliRunner()


# ── test_auth_command_runs ────────────────────────────────────────────────────


class TestAuthCommand:
    def test_auth_command_runs(self, runner):
        """auth calls run_device_code_flow and exits 0 on success."""
        with patch("yoto_cli.commands.misc.run_device_code_flow") as mock_flow:
            result = runner.invoke(cli, ["auth"])
        mock_flow.assert_called_once()
        assert result.exit_code == 0

    def test_auth_converts_auth_error_to_click_exception(self, runner):
        """auth catches AuthError and re-raises as ClickException (exit 1)."""
        with patch(
            "yoto_cli.commands.misc.run_device_code_flow",
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

        with patch("yoto_cli.commands.misc.YotoAPI", return_value=mock_api):
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

        with patch("yoto_cli.commands.misc.YotoAPI", return_value=mock_api):
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

        with patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync:
            result = runner.invoke(cli, ["sync", str(folder)])

        assert result.exit_code == 0
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("dry_run") is False
        assert call_kwargs.get("trim") is True
        assert "log" in call_kwargs
        assert "SYNCED-001" in result.output
        assert "3 tracks" in result.output

    def test_sync_dry_run(self, runner, tmp_path):
        """sync --dry-run prints dry-run message without uploading."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id=None, tracks_uploaded=2, dry_run=True)]

        with patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync:
            result = runner.invoke(cli, ["sync", "--dry-run", str(folder)])

        assert result.exit_code == 0
        mock_sync.assert_called_once_with(folder, dry_run=True, trim=True, ignore_album_art=False, force_cover=False)
        assert "[Dry run]" in result.output
        assert "2 tracks" in result.output


class TestSyncNoTrim:
    def test_sync_passes_no_trim(self, runner, tmp_path):
        """sync --no-trim passes trim=False to sync_path."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1)]

        with patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync:
            result = runner.invoke(cli, ["sync", "--no-trim", str(folder)])

        assert result.exit_code == 0
        assert mock_sync.call_args.kwargs.get("trim") is False or (
            len(mock_sync.call_args) > 0 and mock_sync.call_args[1].get("trim") is False
        )


# ── test_reorder_opens_editor ─────────────────────────────────────────────────


class TestReorderCommand:
    def test_reorder_opens_editor(self, runner, tmp_path):
        """reorder opens $EDITOR; saves reordered content back to the file."""
        playlist_path = tmp_path / "playlist.jsonl"
        original_lines = ['"track_a.mka"', '"track_b.mka"', '"track_c.mka"']
        playlist_path.write_text("\n".join(original_lines) + "\n", encoding="utf-8")

        # Simulate the user swapping b and c
        edited_content = '"track_a.mka"\n"track_c.mka"\n"track_b.mka"\n'

        with patch("yoto_cli.commands.misc.click.edit", return_value=edited_content) as mock_edit:
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

        with patch("yoto_cli.commands.misc.click.edit", return_value=None):
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


# ── test_download_command ─────────────────────────────────────────────────────


class TestDownloadCommand:
    def test_download_resolves_weblocs(self, runner, tmp_path):
        """download calls resolve_weblocs on the given path."""
        folder = tmp_path / "playlist"
        folder.mkdir()
        (folder / "song.webloc").write_bytes(b"fake")

        with patch("yoto_cli.commands.import_cmd.resolve_weblocs", return_value=[]) as mock_resolve:
            result = runner.invoke(cli, ["download", str(folder)])

        assert result.exit_code == 0
        mock_resolve.assert_called_once_with(folder, trim=True, webloc_files=None)

    def test_download_no_trim(self, runner, tmp_path):
        """download --no-trim passes trim=False."""
        folder = tmp_path / "playlist"
        folder.mkdir()

        with patch("yoto_cli.commands.import_cmd.resolve_weblocs", return_value=[]) as mock_resolve:
            result = runner.invoke(cli, ["download", "--no-trim", str(folder)])

        assert result.exit_code == 0
        mock_resolve.assert_called_once_with(folder, trim=False, webloc_files=None)

    def test_download_reports_created_files(self, runner, tmp_path):
        """download prints the names of created .mka files."""
        folder = tmp_path / "playlist"
        folder.mkdir()

        fake_mka = folder / "Cool Song.mka"
        with patch("yoto_cli.commands.import_cmd.resolve_weblocs", return_value=[fake_mka]):
            result = runner.invoke(cli, ["download", str(folder)])

        assert result.exit_code == 0
        assert "Cool Song.mka" in result.output

    def test_download_single_webloc_file(self, runner, tmp_path):
        """download accepts a single .webloc file path."""
        folder = tmp_path / "playlist"
        folder.mkdir()
        webloc = folder / "song.webloc"
        webloc.write_bytes(b"fake")

        with patch("yoto_cli.commands.import_cmd.resolve_weblocs", return_value=[]) as mock_resolve:
            result = runner.invoke(cli, ["download", str(webloc)])

        assert result.exit_code == 0
        mock_resolve.assert_called_once_with(folder, trim=True, webloc_files=[webloc])


# ── test_helpers ─────────────────────────────────────────────────────────────


class TestIsCardId:
    def test_short_alphanumeric(self, tmp_path):
        """Short alphanumeric string that doesn't exist as a path → True."""
        assert _is_card_id("abc12") is True

    def test_existing_path_returns_false(self, tmp_path):
        """A string that matches an existing path → False."""
        assert _is_card_id("/tmp") is False

    def test_too_long(self):
        """String longer than 10 chars → False."""
        assert _is_card_id("abcdefghijk") is False

    def test_empty_string(self):
        """Empty string → False."""
        assert _is_card_id("") is False

    def test_has_special_chars(self):
        """Non-alphanumeric characters → False."""
        assert _is_card_id("abc-12") is False


class TestStripTrackNumber:
    def test_leading_number_space(self):
        assert _strip_track_number("01 Song Name") == "Song Name"

    def test_leading_number_dot(self):
        assert _strip_track_number("01. Song") == "Song"

    def test_leading_number_dash_space(self):
        assert _strip_track_number("01 - Song") == "Song"

    def test_leading_number_dash(self):
        assert _strip_track_number("1-Song") == "Song"

    def test_leading_number_underscore(self):
        assert _strip_track_number("01_Song") == "Song"

    def test_no_number_unchanged(self):
        assert _strip_track_number("Song Name") == "Song Name"

    def test_number_only_returns_original(self):
        """If stripping the number leaves nothing, return original."""
        assert _strip_track_number("01") == "01"


# ── test_pull_command ────────────────────────────────────────────────────────


class TestPullCommand:
    def test_pull_with_path(self, runner, tmp_path):
        """pull with a directory path calls pull_playlist."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / ".yoto-card-id").write_text("CARD99", encoding="utf-8")

        fake_result = PullResult(card_id="CARD99", tracks_downloaded=2)

        with patch("yoto_cli.commands.pull.pull_playlist", return_value=fake_result) as mock_pull:
            result = runner.invoke(cli, ["pull", str(folder)])

        assert result.exit_code == 0
        mock_pull.assert_called_once()
        assert "CARD99" in result.output
        assert "2 tracks" in result.output

    def test_pull_with_card_id(self, runner):
        """pull with a card ID string passes card_id to pull_playlist."""
        fake_result = PullResult(card_id="abc12", tracks_downloaded=1)

        with patch("yoto_cli.commands.pull.pull_playlist", return_value=fake_result) as mock_pull:
            result = runner.invoke(cli, ["pull", "abc12"])

        assert result.exit_code == 0
        call_kwargs = mock_pull.call_args
        assert call_kwargs.kwargs.get("card_id") == "abc12"

    def test_pull_dry_run(self, runner, tmp_path):
        """pull --dry-run passes dry_run=True."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / ".yoto-card-id").write_text("CARD99", encoding="utf-8")

        fake_result = PullResult(card_id="CARD99", dry_run=True)

        with patch("yoto_cli.commands.pull.pull_playlist", return_value=fake_result) as mock_pull:
            result = runner.invoke(cli, ["pull", "--dry-run", str(folder)])

        assert result.exit_code == 0
        assert mock_pull.call_args.kwargs.get("dry_run") is True
        assert "[Dry run]" in result.output

    def test_pull_all(self, runner):
        """pull --all iterates over all cards from the API."""
        fake_cards = [
            {"cardId": "CARD01", "title": "Album 1"},
            {"cardId": "CARD02", "title": "Album 2"},
        ]
        mock_api = MagicMock()
        mock_api.get_my_content.return_value = fake_cards

        fake_result = PullResult(card_id="X", tracks_downloaded=1)

        with (
            runner.isolated_filesystem(),
            patch("yoto_cli.commands.pull.YotoAPI", return_value=mock_api),
            patch("yoto_cli.commands.pull.pull_playlist", return_value=fake_result) as mock_pull,
        ):
            result = runner.invoke(cli, ["pull", "--all"])

        assert result.exit_code == 0
        assert mock_pull.call_count == 2


# ── test_status_command ──────────────────────────────────────────────────────


class TestStatusCommand:
    def test_status_no_changes(self, runner, tmp_path):
        """status prints 'No changes' when local matches remote."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track.mp3").write_bytes(b"\x00" * 16)

        mock_playlist = MagicMock()
        mock_playlist.card_id = "CARD01"
        mock_playlist.track_files = ["track.mp3"]

        from yoto_lib.playlist import PlaylistDiff

        empty_diff = PlaylistDiff(
            new_tracks=[],
            removed_tracks=[],
            order_changed=False,
            cover_changed=False,
            metadata_changed=False,
            icon_changes={},
        )

        with (
            patch("yoto_cli.commands.sync.load_playlist", return_value=mock_playlist),
            patch("yoto_cli.commands.sync.scan_audio_files", return_value=["track.mp3"]),
            patch("yoto_cli.commands.sync.YotoAPI") as mock_api_cls,
            patch("yoto_cli.commands.sync.diff_playlists", return_value=empty_diff),
            patch("yoto_lib.sync._parse_remote_state", return_value={}),
        ):
            mock_api_cls.return_value.get_content.return_value = {}
            result = runner.invoke(cli, ["status", str(folder)])

        assert result.exit_code == 0
        assert "No changes" in result.output

    def test_status_shows_new_tracks(self, runner, tmp_path):
        """status lists new tracks with + prefix."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "new_song.mp3").write_bytes(b"\x00" * 16)

        mock_playlist = MagicMock()
        mock_playlist.card_id = None
        mock_playlist.track_files = ["new_song.mp3"]

        from yoto_lib.playlist import PlaylistDiff

        diff = PlaylistDiff(
            new_tracks=["new_song.mp3"],
            removed_tracks=[],
            order_changed=False,
            cover_changed=False,
            metadata_changed=False,
            icon_changes={},
        )

        with (
            patch("yoto_cli.commands.sync.load_playlist", return_value=mock_playlist),
            patch("yoto_cli.commands.sync.scan_audio_files", return_value=["new_song.mp3"]),
            patch("yoto_cli.commands.sync.diff_playlists", return_value=diff),
        ):
            result = runner.invoke(cli, ["status", str(folder)])

        assert result.exit_code == 0
        assert "+ new_song.mp3" in result.output

    def test_status_not_a_playlist(self, runner, tmp_path):
        """status on empty folder raises error."""
        folder = tmp_path / "empty"
        folder.mkdir()

        with patch("yoto_cli.commands.sync.scan_audio_files", return_value=[]):
            result = runner.invoke(cli, ["status", str(folder)])

        assert result.exit_code != 0
        assert "Not a playlist folder" in result.output


# ── test_export_command ──────────────────────────────────────────────────────


class TestExportCommand:
    def test_export_no_mka_files(self, runner, tmp_path):
        """export with no MKA files prints message."""
        folder = tmp_path / "album"
        folder.mkdir()

        result = runner.invoke(cli, ["export", str(folder)])

        assert result.exit_code == 0
        assert "No .mka files found" in result.output

    def test_export_extracts_without_patch(self, runner, tmp_path):
        """export extracts audio when no bsdiff patch is stored."""
        folder = tmp_path / "album"
        folder.mkdir()
        mka = folder / "song.mka"
        mka.write_bytes(b"fake")

        output_dir = tmp_path / "output"
        extracted = output_dir / "song.ogg"

        with (
            patch("yoto_lib.mka.get_attachment", return_value=None),
            patch("yoto_lib.mka.extract_audio", return_value=extracted),
        ):
            result = runner.invoke(cli, ["export", str(folder), "-o", str(output_dir)])

        assert result.exit_code == 0

    def test_export_byte_perfect_with_patch(self, runner, tmp_path):
        """export applies bsdiff patch for byte-perfect output."""
        folder = tmp_path / "album"
        folder.mkdir()
        mka = folder / "song.mka"
        mka.write_bytes(b"fake")
        output_dir = tmp_path / "out"

        with (
            patch("yoto_lib.mka.get_attachment", return_value=b"patch_data"),
            patch("yoto_lib.mka.extract_audio") as mock_extract,
            patch("yoto_lib.mka.apply_source_patch", return_value=True),
        ):
            mock_extract.return_value = tmp_path / "tmp" / "song.ogg"
            result = runner.invoke(cli, ["export", str(folder), "-o", str(output_dir)])

        assert result.exit_code == 0


# ── test_import_command ──────────────────────────────────────────────────────


class TestImportCommand:
    def test_import_no_audio_files(self, runner, tmp_path):
        """import with no audio files prints message."""
        folder = tmp_path / "empty"
        folder.mkdir()

        result = runner.invoke(cli, ["import", str(folder)])

        assert result.exit_code == 0
        assert "No audio files found" in result.output

    def test_import_wraps_and_writes_jsonl(self, runner, tmp_path):
        """import wraps audio files, writes tags, writes playlist.jsonl."""
        folder = tmp_path / "music"
        folder.mkdir()
        (folder / "01 Song One.mp3").write_bytes(b"\x00" * 64)
        (folder / "02 Song Two.mp3").write_bytes(b"\x00" * 64)

        with (
            patch("yoto_cli.commands.import_cmd.wrap_in_mka") as mock_wrap,
            patch("yoto_cli.commands.import_cmd.read_source_tags", return_value={"title": "Song", "artist": "Bob"}),
            patch("yoto_cli.commands.import_cmd.write_tags"),
            patch("yoto_cli.commands.import_cmd.enrich_from_itunes"),
            patch("yoto_cli.commands.import_cmd.generate_source_patch"),
            patch("yoto_cli.commands.import_cmd.generate_description"),
            patch("yoto_cli.commands.import_cmd.load_playlist") as mock_load,
        ):
            mock_load.return_value = MagicMock()
            result = runner.invoke(cli, ["import", str(folder)])

        assert result.exit_code == 0, result.output
        assert mock_wrap.call_count == 2
        # playlist.jsonl should have been written
        jsonl = folder / "playlist.jsonl"
        assert jsonl.exists()


# ── test_cover_command ───────────────────────────────────────────────────────


class TestCoverCommand:
    def test_cover_already_exists(self, runner, tmp_path):
        """cover command exits early when cover.png exists (no --force)."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "cover.png").write_bytes(b"\x89PNG")
        (folder / "playlist.jsonl").write_text('"track.mka"\n', encoding="utf-8")
        (folder / "track.mka").write_bytes(b"\x00" * 64)

        with patch("yoto_cli.commands.cover.load_playlist") as mock_load:
            mock_playlist = MagicMock()
            mock_playlist.cover_path = folder / "cover.png"
            mock_playlist.track_files = ["track.mka"]
            mock_load.return_value = mock_playlist
            result = runner.invoke(cli, ["cover", str(folder)])

        assert result.exit_code == 0
        assert "Cover already exists" in result.output


# ── test_reset_icon_command ──────────────────────────────────────────────────


class TestResetIconCommand:
    def test_reset_icon_removes_attachment(self, runner, tmp_path):
        """reset-icon calls remove_attachment and clear_macos_file_icon."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")

        with (
            patch("yoto_cli.commands.icons.remove_attachment") as mock_remove,
            patch("yoto_lib.icons.clear_macos_file_icon") as mock_clear,
        ):
            result = runner.invoke(cli, ["reset-icon", str(mka)])

        assert result.exit_code == 0
        mock_remove.assert_called_once_with(mka, "icon")
        mock_clear.assert_called_once_with(mka)
        assert "Cleared icon" in result.output

    def test_reset_icon_handles_errors(self, runner, tmp_path):
        """reset-icon prints error when remove_attachment fails."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")

        with patch("yoto_cli.commands.icons.remove_attachment", side_effect=OSError("mkvpropedit failed")):
            result = runner.invoke(cli, ["reset-icon", str(mka)])

        assert result.exit_code == 0
        assert "Error" in result.output


# ── test_print_command ───────────────────────────────────────────────────────


class TestPrintCommand:
    def test_print_sends_to_printer(self, runner, tmp_path):
        """print calls print_cover on the playlist's cover.png."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        # Create a valid cover
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        with patch("yoto_cli.commands.cover.print_cover") as mock_print, patch.dict(os.environ, {}, clear=False) as env:
            env.pop("YOTO_ICC_PROFILE", None)
            result = runner.invoke(cli, ["print", str(folder)], input="y\n")

        assert result.exit_code == 0
        mock_print.assert_called_once()
        assert mock_print.call_args[0][0] == folder / "cover.png"
        assert mock_print.call_args[1]["icc_profile"] is None

    def test_print_yes_skips_confirm(self, runner, tmp_path):
        """print --yes skips the confirmation prompt."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        with patch("yoto_cli.commands.cover.print_cover") as mock_print:
            result = runner.invoke(cli, ["print", "--yes", str(folder)])

        assert result.exit_code == 0
        mock_print.assert_called_once()

    def test_print_no_cover_offers_generation(self, runner, tmp_path):
        """print offers to generate cover when cover.png is missing."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        (folder / "description.txt").write_text("A test playlist")

        with (
            patch("yoto_cli.commands.cover.generate_cover_if_missing") as mock_gen,
            patch("yoto_cli.commands.cover.print_cover") as mock_print,
        ):
            # Answer "y" to generate, then "y" to print
            result = runner.invoke(cli, ["print", str(folder)], input="y\ny\n")

        mock_gen.assert_called_once()

    def test_print_no_cover_decline_generation(self, runner, tmp_path):
        """print exits cleanly when user declines cover generation."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)

        with patch("yoto_cli.commands.cover.generate_cover_if_missing") as mock_gen:
            result = runner.invoke(cli, ["print", str(folder)], input="n\n")

        mock_gen.assert_not_called()
        assert result.exit_code == 0

    def test_print_with_profile(self, runner, tmp_path):
        """print --profile passes ICC profile to print_cover."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")
        fake_profile = tmp_path / "test.icc"
        fake_profile.write_bytes(b"fake")

        with patch("yoto_cli.commands.cover.print_cover") as mock_print:
            result = runner.invoke(cli, ["print", "--yes", "--profile", str(fake_profile), str(folder)])

        assert result.exit_code == 0
        mock_print.assert_called_once()
        assert mock_print.call_args[1]["icc_profile"] == str(fake_profile)

    def test_print_missing_profile_warns(self, runner, tmp_path):
        """print warns and offers to continue when profile not found."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        with patch("yoto_cli.commands.cover.print_cover") as mock_print:
            # Answer "y" to continue without color management, "y" to print
            result = runner.invoke(cli, ["print", "--profile", "/nonexistent.icc", str(folder)], input="y\ny\n")

        assert result.exit_code == 0
        mock_print.assert_called_once()
        assert mock_print.call_args[1]["icc_profile"] is None

    def test_print_error_shows_message(self, runner, tmp_path):
        """PrintError is surfaced as a ClickException."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        with patch("yoto_cli.commands.cover.print_cover", side_effect=PrintError("Printer offline")):
            result = runner.invoke(cli, ["print", "--yes", str(folder)])

        assert result.exit_code != 0
        assert "Printer offline" in result.output


# ── test_sync_print ──────────────────────────────────────────────────────────


class TestSyncPrint:
    def test_sync_print_flag(self, runner, tmp_path):
        """sync --print calls print_cover when cover was uploaded."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=True, folder=folder)]

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync,
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            result = runner.invoke(cli, ["sync", "--print", str(folder)])

        assert result.exit_code == 0
        mock_print.assert_called_once()
        assert mock_print.call_args[0][0] == folder / "cover.png"

    def test_sync_no_print_flag(self, runner, tmp_path):
        """sync --no-print skips printing even when cover was uploaded."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=True, folder=folder)]

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync,
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            result = runner.invoke(cli, ["sync", "--no-print", str(folder)])

        assert result.exit_code == 0
        mock_print.assert_not_called()

    def test_sync_prompts_when_cover_uploaded(self, runner, tmp_path):
        """sync prompts to print when cover was uploaded and no flag given."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=True, folder=folder)]

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync,
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            result = runner.invoke(cli, ["sync", str(folder)], input="y\n")

        mock_print.assert_called_once()

    def test_sync_no_prompt_when_cover_not_uploaded(self, runner, tmp_path):
        """sync does not prompt to print when cover was not uploaded."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=False, folder=folder)]

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync,
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            result = runner.invoke(cli, ["sync", str(folder)])

        assert result.exit_code == 0
        mock_print.assert_not_called()

    def test_sync_dry_run_never_prints(self, runner, tmp_path):
        """sync --dry-run never prints, regardless of --print flag."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id=None, tracks_uploaded=2, dry_run=True, cover_uploaded=True, folder=folder)]

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync,
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            result = runner.invoke(cli, ["sync", "--dry-run", "--print", str(folder)])

        assert result.exit_code == 0
        mock_print.assert_not_called()

    def test_sync_print_error_shown_as_warning(self, runner, tmp_path):
        """If printing fails during sync, show a warning but don't fail the sync."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=True, folder=folder)]

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results),
            patch("yoto_cli.commands.sync.print_cover", side_effect=PrintError("Printer offline")),
        ):
            result = runner.invoke(cli, ["sync", "--print", str(folder)])

        assert result.exit_code == 0
        assert "Printer offline" in result.output
