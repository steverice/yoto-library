"""Tests for yoto_cli — CLI layer."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yoto_cli.main import _is_card_id, _strip_track_number, build_parser
from yoto_lib.covers.printer import PrintError
from yoto_lib.pull import PullResult
from yoto_lib.sync import SyncResult
from yoto_lib.yoto.auth import AuthError

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _png_bytes() -> bytes:
    """A small valid PNG, for tests that need real image bytes."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buf, format="PNG")
    return buf.getvalue()


# ── test_auth_command_runs ────────────────────────────────────────────────────


class TestAuthCommand:
    def test_auth_parses(self):
        parser = build_parser()
        args = parser.parse_args(["auth"])
        assert args.command == "auth"
        assert hasattr(args, "func")

    def test_auth_command_runs(self):
        with patch("yoto_cli.commands.misc.run_device_code_flow") as mock_flow:
            from yoto_cli.commands.misc import handle_auth

            handle_auth(argparse.Namespace())
        mock_flow.assert_called_once()

    def test_auth_converts_auth_error_to_exit(self):
        from yoto_cli.commands.misc import handle_auth

        with (
            patch("yoto_cli.commands.misc.run_device_code_flow", side_effect=AuthError("Device code expired")),
            pytest.raises(SystemExit),
        ):
            handle_auth(argparse.Namespace())


# ── test_list_shows_cards ─────────────────────────────────────────────────────


class TestListCommand:
    def test_list_shows_cards(self):
        fake_cards = [
            {"cardId": "CARD001", "title": "My Album"},
            {"cardId": "CARD002", "title": "Kids Stories"},
        ]
        mock_api = MagicMock()
        mock_api.get_my_content.return_value = fake_cards
        mock_api.get_content.return_value = {"content": {"chapters": []}}

        with patch("yoto_cli.commands.misc.YotoAPI", return_value=mock_api):
            from yoto_cli.commands.misc import handle_list

            handle_list(argparse.Namespace())

    def test_list_empty_account(self):
        mock_api = MagicMock()
        mock_api.get_my_content.return_value = []

        with patch("yoto_cli.commands.misc.YotoAPI", return_value=mock_api):
            from yoto_cli.commands.misc import handle_list

            handle_list(argparse.Namespace())


# ── test_sync_runs ────────────────────────────────────────────────────────────


class TestSyncCommand:
    def test_sync_parses(self):
        parser = build_parser()
        args = parser.parse_args(["sync", "/some/path"])
        assert args.command == "sync"
        assert hasattr(args, "func")

    def test_sync_runs(self, tmp_path):
        """sync calls sync_path and prints card + track count."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id="SYNCED-001", tracks_uploaded=3)]

        from yoto_cli.commands.sync import handle_sync

        with patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync:
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=False,
                    no_trim=False,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=None,
                )
            )

        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("dry_run") is False
        assert call_kwargs.get("trim") is True
        assert "log" in call_kwargs

    def test_sync_dry_run(self, tmp_path):
        """sync --dry-run prints dry-run message without uploading."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id=None, tracks_uploaded=2, dry_run=True)]

        from yoto_cli.commands.sync import handle_sync

        with patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync:
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=True,
                    no_trim=False,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=None,
                )
            )

        mock_sync.assert_called_once_with(folder, dry_run=True, trim=True, ignore_album_art=False, force_cover=False)


class TestSyncNoTrim:
    def test_sync_passes_no_trim(self, tmp_path):
        """sync --no-trim passes trim=False to sync_path."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1)]

        from yoto_cli.commands.sync import handle_sync

        with patch("yoto_cli.commands.sync.sync_path", return_value=fake_results) as mock_sync:
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=False,
                    no_trim=True,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=None,
                )
            )

        assert mock_sync.call_args.kwargs.get("trim") is False


# ── test_reorder_opens_editor ─────────────────────────────────────────────────


class TestReorderCommand:
    def test_reorder_opens_editor(self, tmp_path):
        """reorder opens $EDITOR; saves reordered content back to the file."""
        playlist_path = tmp_path / "playlist.jsonl"
        original_lines = ['"track_a.mka"', '"track_b.mka"', '"track_c.mka"']
        playlist_path.write_text("\n".join(original_lines) + "\n", encoding="utf-8")

        # Simulate the user swapping b and c
        edited_content = '"track_a.mka"\n"track_c.mka"\n"track_b.mka"\n'

        from yoto_cli.commands.misc import handle_reorder

        with patch("yoto_cli.main._open_editor", return_value=edited_content) as mock_edit:
            handle_reorder(argparse.Namespace(playlist=playlist_path))

        mock_edit.assert_called_once()

        saved = playlist_path.read_text(encoding="utf-8")
        saved_names = [json.loads(line) for line in saved.splitlines() if line.strip()]
        assert saved_names == ["track_a.mka", "track_c.mka", "track_b.mka"]

    def test_reorder_no_changes(self, tmp_path):
        """reorder prints 'No changes' when editor returns None."""
        playlist_path = tmp_path / "playlist.jsonl"
        playlist_path.write_text('"track_a.mka"\n', encoding="utf-8")

        from yoto_cli.commands.misc import handle_reorder

        with patch("yoto_cli.main._open_editor", return_value=None):
            handle_reorder(argparse.Namespace(playlist=playlist_path))

    def test_reorder_file_not_found(self, tmp_path):
        """reorder exits with error when file does not exist."""
        from yoto_cli.commands.misc import handle_reorder

        with pytest.raises(SystemExit):
            handle_reorder(argparse.Namespace(playlist=tmp_path / "nonexistent.jsonl"))

    def test_reorder_invalid_json(self, tmp_path):
        """reorder exits with error on invalid JSON."""
        playlist_path = tmp_path / "playlist.jsonl"
        playlist_path.write_text('"track_a.mka"\n', encoding="utf-8")

        from yoto_cli.commands.misc import handle_reorder

        with (
            patch("yoto_cli.main._open_editor", return_value="not valid json\n"),
            pytest.raises(SystemExit),
        ):
            handle_reorder(argparse.Namespace(playlist=playlist_path))


# ── test_init_creates_folder ──────────────────────────────────────────────────


class TestInitCommand:
    def test_init_creates_folder(self, tmp_path):
        new_folder = tmp_path / "my-new-playlist"
        from yoto_cli.commands.misc import handle_init

        handle_init(argparse.Namespace(path=new_folder))
        assert new_folder.exists()
        jsonl_path = new_folder / "playlist.jsonl"
        assert jsonl_path.exists()
        content = jsonl_path.read_text(encoding="utf-8")
        filenames = [json.loads(ln) for ln in content.splitlines() if ln.strip()]
        assert filenames == []

    def test_init_existing_folder(self, tmp_path):
        folder = tmp_path / "existing"
        folder.mkdir()
        jsonl = folder / "playlist.jsonl"
        jsonl.write_text('"track.mka"\n', encoding="utf-8")
        from yoto_cli.commands.misc import handle_init

        handle_init(argparse.Namespace(path=folder))
        assert json.loads(jsonl.read_text().strip()) == "track.mka"


# ── test_download_command ─────────────────────────────────────────────────────


class TestDownloadCommand:
    def test_download_parses(self):
        parser = build_parser()
        args = parser.parse_args(["download", "/some/path"])
        assert args.command == "download"
        assert hasattr(args, "func")

    def test_download_resolves_weblocs(self, tmp_path):
        """download calls resolve_weblocs on the given path."""
        folder = tmp_path / "playlist"
        folder.mkdir()
        (folder / "song.webloc").write_bytes(b"fake")

        from yoto_cli.commands.import_cmd import handle_download

        with patch("yoto_cli.commands.import_cmd.resolve_weblocs", return_value=[]) as mock_resolve:
            handle_download(argparse.Namespace(path=folder, no_trim=False))

        mock_resolve.assert_called_once_with(folder, trim=True, webloc_files=None)

    def test_download_no_trim(self, tmp_path):
        """download --no-trim passes trim=False."""
        folder = tmp_path / "playlist"
        folder.mkdir()

        from yoto_cli.commands.import_cmd import handle_download

        with patch("yoto_cli.commands.import_cmd.resolve_weblocs", return_value=[]) as mock_resolve:
            handle_download(argparse.Namespace(path=folder, no_trim=True))

        mock_resolve.assert_called_once_with(folder, trim=False, webloc_files=None)

    def test_download_reports_created_files(self, tmp_path, capsys):
        """download prints the names of created .mka files."""
        folder = tmp_path / "playlist"
        folder.mkdir()

        fake_mka = folder / "Cool Song.mka"

        from yoto_cli.commands.import_cmd import handle_download

        with patch("yoto_cli.commands.import_cmd.resolve_weblocs", return_value=[fake_mka]):
            handle_download(argparse.Namespace(path=folder, no_trim=False))

    def test_download_single_webloc_file(self, tmp_path):
        """download accepts a single .webloc file path."""
        folder = tmp_path / "playlist"
        folder.mkdir()
        webloc = folder / "song.webloc"
        webloc.write_bytes(b"fake")

        from yoto_cli.commands.import_cmd import handle_download

        with patch("yoto_cli.commands.import_cmd.resolve_weblocs", return_value=[]) as mock_resolve:
            handle_download(argparse.Namespace(path=webloc, no_trim=False))

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
    def test_pull_parses(self):
        parser = build_parser()
        args = parser.parse_args(["pull", "/some/path"])
        assert args.command == "pull"
        assert hasattr(args, "func")

    def test_pull_with_path(self, tmp_path):
        """pull with a directory path calls pull_playlist."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / ".yoto-card-id").write_text("CARD99", encoding="utf-8")

        fake_result = PullResult(card_id="CARD99", tracks_downloaded=2)

        from yoto_cli.commands.pull import handle_pull

        with patch("yoto_cli.commands.pull.pull_playlist", return_value=fake_result) as mock_pull:
            handle_pull(argparse.Namespace(path_or_card_id=str(folder), dry_run=False, pull_all=False))

        mock_pull.assert_called_once()

    def test_pull_with_card_id(self):
        """pull with a card ID string passes card_id to pull_playlist."""
        fake_result = PullResult(card_id="abc12", tracks_downloaded=1)

        from yoto_cli.commands.pull import handle_pull

        with patch("yoto_cli.commands.pull.pull_playlist", return_value=fake_result) as mock_pull:
            handle_pull(argparse.Namespace(path_or_card_id="abc12", dry_run=False, pull_all=False))

        call_kwargs = mock_pull.call_args
        assert call_kwargs.kwargs.get("card_id") == "abc12"

    def test_pull_dry_run(self, tmp_path):
        """pull --dry-run passes dry_run=True."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / ".yoto-card-id").write_text("CARD99", encoding="utf-8")

        fake_result = PullResult(card_id="CARD99", dry_run=True)

        from yoto_cli.commands.pull import handle_pull

        with patch("yoto_cli.commands.pull.pull_playlist", return_value=fake_result) as mock_pull:
            handle_pull(argparse.Namespace(path_or_card_id=str(folder), dry_run=True, pull_all=False))

        assert mock_pull.call_args.kwargs.get("dry_run") is True

    def test_pull_all(self, tmp_path, monkeypatch):
        """pull --all iterates over all cards from the API."""
        monkeypatch.chdir(tmp_path)
        fake_cards = [
            {"cardId": "CARD01", "title": "Album 1"},
            {"cardId": "CARD02", "title": "Album 2"},
        ]
        mock_api = MagicMock()
        mock_api.get_my_content.return_value = fake_cards

        fake_result = PullResult(card_id="X", tracks_downloaded=1)

        from yoto_cli.commands.pull import handle_pull

        with (
            patch("yoto_cli.commands.pull.YotoAPI", return_value=mock_api),
            patch("yoto_cli.commands.pull.pull_playlist", return_value=fake_result) as mock_pull,
        ):
            handle_pull(argparse.Namespace(path_or_card_id=".", dry_run=False, pull_all=True))

        assert mock_pull.call_count == 2


# ── test_status_command ──────────────────────────────────────────────────────


class TestStatusCommand:
    def test_status_no_changes(self, tmp_path):
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

        from yoto_cli.commands.sync import handle_status

        with (
            patch("yoto_cli.commands.sync.load_playlist", return_value=mock_playlist),
            patch("yoto_cli.commands.sync.scan_audio_files", return_value=["track.mp3"]),
            patch("yoto_cli.commands.sync.YotoAPI") as mock_api_cls,
            patch("yoto_cli.commands.sync.diff_playlists", return_value=empty_diff),
            patch("yoto_lib.sync._parse_remote_state", return_value={}),
        ):
            mock_api_cls.return_value.get_content.return_value = {}
            handle_status(argparse.Namespace(path=folder))

    def test_status_shows_new_tracks(self, tmp_path, capsys):
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

        from yoto_cli.commands.sync import handle_status

        with (
            patch("yoto_cli.commands.sync.load_playlist", return_value=mock_playlist),
            patch("yoto_cli.commands.sync.scan_audio_files", return_value=["new_song.mp3"]),
            patch("yoto_cli.commands.sync.diff_playlists", return_value=diff),
        ):
            handle_status(argparse.Namespace(path=folder))

    def test_status_not_a_playlist(self, tmp_path):
        """status on empty folder raises error."""
        folder = tmp_path / "empty"
        folder.mkdir()

        from yoto_cli.commands.sync import handle_status

        with (
            patch("yoto_cli.commands.sync.scan_audio_files", return_value=[]),
            pytest.raises(SystemExit),
        ):
            handle_status(argparse.Namespace(path=folder))


# ── test_export_command ──────────────────────────────────────────────────────


class TestExportCommand:
    def test_export_no_mka_files(self, tmp_path):
        """export with no MKA files prints message."""
        folder = tmp_path / "album"
        folder.mkdir()

        from yoto_cli.commands.misc import handle_export

        handle_export(argparse.Namespace(playlist=folder, output=None))

    def test_export_extracts_without_patch(self, tmp_path):
        """export extracts audio when no bsdiff patch is stored."""
        folder = tmp_path / "album"
        folder.mkdir()
        mka = folder / "song.mka"
        mka.write_bytes(b"fake")

        output_dir = tmp_path / "output"
        extracted = output_dir / "song.ogg"

        from yoto_cli.commands.misc import handle_export

        with (
            patch("yoto_lib.mka.get_attachment", return_value=None),
            patch("yoto_lib.mka.extract_audio", return_value=extracted),
        ):
            handle_export(argparse.Namespace(playlist=folder, output=output_dir))

    def test_export_byte_perfect_with_patch(self, tmp_path):
        """export applies bsdiff patch for byte-perfect output."""
        folder = tmp_path / "album"
        folder.mkdir()
        mka = folder / "song.mka"
        mka.write_bytes(b"fake")
        output_dir = tmp_path / "out"

        from yoto_cli.commands.misc import handle_export

        with (
            patch("yoto_lib.mka.get_attachment", return_value=b"patch_data"),
            patch("yoto_lib.mka.extract_audio") as mock_extract,
            patch("yoto_lib.mka.apply_source_patch", return_value=True),
        ):
            mock_extract.return_value = tmp_path / "tmp" / "song.ogg"
            handle_export(argparse.Namespace(playlist=folder, output=output_dir))

    def test_export_nonexistent_path(self, tmp_path):
        """export exits with error when path does not exist."""
        from yoto_cli.commands.misc import handle_export

        with pytest.raises(SystemExit):
            handle_export(argparse.Namespace(playlist=tmp_path / "nonexistent", output=None))


# ── test_import_command ──────────────────────────────────────────────────────


class TestImportCommand:
    def test_import_parses(self):
        parser = build_parser()
        args = parser.parse_args(["import", "/some/path"])
        assert args.command == "import"
        assert hasattr(args, "func")

    def test_import_no_audio_files(self, tmp_path):
        """import with no audio files prints message."""
        folder = tmp_path / "empty"
        folder.mkdir()

        from yoto_cli.commands.import_cmd import handle_import

        handle_import(argparse.Namespace(source=folder, output=None))

    def test_import_wraps_and_writes_jsonl(self, tmp_path):
        """import wraps audio files, writes tags, writes playlist.jsonl."""
        folder = tmp_path / "music"
        folder.mkdir()
        (folder / "01 Song One.mp3").write_bytes(b"\x00" * 64)
        (folder / "02 Song Two.mp3").write_bytes(b"\x00" * 64)

        from yoto_cli.commands.import_cmd import handle_import

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
            handle_import(argparse.Namespace(source=folder, output=None))

        assert mock_wrap.call_count == 2
        # playlist.jsonl should have been written
        jsonl = folder / "playlist.jsonl"
        assert jsonl.exists()

    def test_import_nonexistent_path(self, tmp_path):
        """import exits with error when source path does not exist."""
        from yoto_cli.commands.import_cmd import handle_import

        with pytest.raises(SystemExit):
            handle_import(argparse.Namespace(source=tmp_path / "nonexistent", output=None))


# ── test_cover_command ───────────────────────────────────────────────────────


class TestCoverCommand:
    @pytest.fixture(autouse=True)
    def _no_provider_keys(self, monkeypatch):
        """Strip provider API keys so cover tests never hit a real API via leaked env."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def test_cover_parses(self):
        parser = build_parser()
        args = parser.parse_args(["cover", "/some/path"])
        assert args.command == "cover"
        assert hasattr(args, "func")

    def test_cover_already_exists(self, tmp_path):
        """cover command exits early when cover.png exists (no --force)."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "cover.png").write_bytes(b"\x89PNG")
        (folder / "playlist.jsonl").write_text('"track.mka"\n', encoding="utf-8")
        (folder / "track.mka").write_bytes(b"\x00" * 64)

        from yoto_cli.commands.cover import handle_cover

        with patch("yoto_cli.commands.cover.load_playlist") as mock_load:
            mock_playlist = MagicMock()
            mock_playlist.cover_path = folder / "cover.png"
            mock_playlist.track_files = ["track.mka"]
            mock_load.return_value = mock_playlist
            handle_cover(
                argparse.Namespace(
                    path=folder,
                    force=False,
                    backup=False,
                    ignore_album_art=False,
                    style=None,
                    model=None,
                    reference=None,
                )
            )

    def test_cover_mutual_exclusion(self):
        """cover --force and --backup are mutually exclusive."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["cover", "--force", "--backup", "/some/path"])

    def test_cover_parses_model_and_reference(self, tmp_path):
        parser = build_parser()
        args = parser.parse_args(
            ["cover", str(tmp_path), "--model", "gemini", "--reference", str(tmp_path / "r.png")]
        )
        assert args.model == "gemini"
        assert args.reference == tmp_path / "r.png"

    def _cover_playlist(self, folder, title=""):
        playlist = MagicMock()
        playlist.cover_path = folder / "cover.png"
        playlist.description_path.exists.return_value = True
        playlist.track_files = []
        playlist.title = title
        playlist.style = "storybook"
        return playlist

    def test_cover_openai_with_reference_errors(self, tmp_path, monkeypatch):
        """--model openai with --reference is rejected before any generation."""
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        folder = tmp_path / "album"
        folder.mkdir()
        ref = tmp_path / "r.png"
        from PIL import Image

        Image.new("RGB", (32, 32), "blue").save(ref)

        from yoto_cli.commands.cover import handle_cover

        with (
            patch("yoto_cli.commands.cover.load_playlist", return_value=self._cover_playlist(folder)),
            patch("yoto_lib.covers.cover.generate_cover_illustration") as mock_gen,
            pytest.raises(SystemExit),
        ):
            handle_cover(
                argparse.Namespace(
                    path=folder, force=False, backup=False, ignore_album_art=True, style=None,
                    model="openai", reference=ref,
                )
            )

        mock_gen.assert_not_called()

    def test_cover_reference_routes_to_gemini(self, tmp_path, monkeypatch):
        """A reference image routes step 1 to Gemini and forwards the bytes."""
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        folder = tmp_path / "album"
        folder.mkdir()
        ref = tmp_path / "r.png"
        from PIL import Image

        Image.new("RGB", (32, 32), "blue").save(ref)

        from yoto_cli.commands.cover import handle_cover

        with (
            patch("yoto_cli.commands.cover.load_playlist", return_value=self._cover_playlist(folder)),
            patch("yoto_lib.covers.cover.build_cover_prompt", return_value="a prompt"),
            patch("yoto_lib.covers.cover.generate_cover_illustration", return_value=b"img") as mock_gen,
            patch("yoto_lib.covers.cover.resize_cover"),
        ):
            handle_cover(
                argparse.Namespace(
                    path=folder, force=False, backup=False, ignore_album_art=True, style=None,
                    model=None, reference=ref,
                )
            )

        kwargs = mock_gen.call_args.kwargs
        assert kwargs["model"] == "gemini"
        assert kwargs["reference_image"] == ref.read_bytes()


# ── test_reset_icon_command ──────────────────────────────────────────────────


class TestResetIconCommand:
    def test_reset_icon_parses(self, tmp_path):
        mka = tmp_path / "track.mka"
        parser = build_parser()
        args = parser.parse_args(["reset-icon", str(mka)])
        assert args.command == "reset-icon"
        assert hasattr(args, "func")
        assert args.tracks == [mka]

    def test_reset_icon_removes_attachment(self, tmp_path):
        """reset-icon calls remove_attachment and clear_macos_file_icon."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")

        from yoto_cli.commands.icons import handle_reset_icon

        with (
            patch("yoto_cli.commands.icons.remove_attachment") as mock_remove,
            patch("yoto_lib.icons.clear_macos_file_icon") as mock_clear,
        ):
            handle_reset_icon(argparse.Namespace(tracks=[mka]))

        mock_remove.assert_called_once_with(mka, "icon")
        mock_clear.assert_called_once_with(mka)

    def test_reset_icon_handles_errors(self, tmp_path):
        """reset-icon prints error when remove_attachment fails."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")

        from yoto_cli.commands.icons import handle_reset_icon

        with patch("yoto_cli.commands.icons.remove_attachment", side_effect=OSError("mkvpropedit failed")):
            handle_reset_icon(argparse.Namespace(tracks=[mka]))

    def test_reset_icon_ignores_non_mka_files(self, tmp_path):
        """reset-icon silently drops non-.mka files from a glob (e.g. description.txt)."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")
        txt = tmp_path / "description.txt"
        txt.write_text("not audio")

        from yoto_cli.commands.icons import handle_reset_icon

        with (
            patch("yoto_cli.commands.icons.remove_attachment") as mock_remove,
            patch("yoto_lib.icons.clear_macos_file_icon"),
        ):
            handle_reset_icon(argparse.Namespace(tracks=[mka, txt]))

        mock_remove.assert_called_once_with(mka, "icon")

    def test_reset_icon_skips_work_when_no_mka_files(self, tmp_path):
        """reset-icon does nothing when no .mka files are given."""
        txt = tmp_path / "description.txt"
        txt.write_text("not audio")

        from yoto_cli.commands.icons import handle_reset_icon

        with patch("yoto_cli.commands.icons.remove_attachment") as mock_remove:
            handle_reset_icon(argparse.Namespace(tracks=[txt]))

        mock_remove.assert_not_called()


class TestSelectIconCommand:
    def test_select_icon_ignores_non_mka_files(self, tmp_path):
        """select-icon silently drops non-.mka files from a glob (e.g. description.txt)."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")
        txt = tmp_path / "description.txt"
        txt.write_text("not audio")

        from yoto_cli.commands.icons import handle_select_icon

        with (
            patch("yoto_cli.commands.icons.YotoAPI"),
            patch("yoto_lib.icons.select.select_icons_for_tracks") as mock_select,
        ):
            handle_select_icon(argparse.Namespace(tracks=[mka, txt], reference=None, strength=None))

        passed_tracks = mock_select.call_args.args[0]
        assert passed_tracks == [mka]

    def test_select_icon_skips_work_when_no_mka_files(self, tmp_path):
        """select-icon does nothing (no API call) when no .mka files are given."""
        txt = tmp_path / "description.txt"
        txt.write_text("not audio")

        from yoto_cli.commands.icons import handle_select_icon

        with (
            patch("yoto_cli.commands.icons.YotoAPI") as mock_api,
            patch("yoto_lib.icons.select.select_icons_for_tracks") as mock_select,
        ):
            handle_select_icon(argparse.Namespace(tracks=[txt], reference=None, strength=None))

        mock_select.assert_not_called()
        mock_api.assert_not_called()

    def test_select_icon_parses_reference_and_strength(self, tmp_path):
        parser = build_parser()
        args = parser.parse_args(
            ["select-icon", str(tmp_path / "t.mka"), "--reference", str(tmp_path / "r.png"), "--strength", "0.4"]
        )
        assert args.reference == tmp_path / "r.png"
        assert args.strength == 0.4

    def test_select_icon_passes_reference_bytes_and_strength(self, tmp_path):
        """A valid reference is read and forwarded with the given strength."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")
        ref = tmp_path / "ref.png"
        from PIL import Image

        Image.new("RGB", (32, 32), "blue").save(ref)

        from yoto_cli.commands.icons import handle_select_icon

        with (
            patch("yoto_cli.commands.icons.YotoAPI"),
            patch("yoto_lib.icons.select.select_icons_for_tracks") as mock_select,
        ):
            handle_select_icon(argparse.Namespace(tracks=[mka], reference=ref, strength=0.5))

        kwargs = mock_select.call_args.kwargs
        assert kwargs["reference_image"] == ref.read_bytes()
        assert kwargs["strength"] == 0.5

    def test_select_icon_reference_defaults_strength(self, tmp_path):
        """When --strength is omitted, the default reference strength is used."""
        from yoto_lib.icons.generate import DEFAULT_REFERENCE_STRENGTH

        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")
        ref = tmp_path / "ref.png"
        from PIL import Image

        Image.new("RGB", (32, 32), "blue").save(ref)

        from yoto_cli.commands.icons import handle_select_icon

        with (
            patch("yoto_cli.commands.icons.YotoAPI"),
            patch("yoto_lib.icons.select.select_icons_for_tracks") as mock_select,
        ):
            handle_select_icon(argparse.Namespace(tracks=[mka], reference=ref, strength=None))

        assert mock_select.call_args.kwargs["strength"] == DEFAULT_REFERENCE_STRENGTH

    def test_select_icon_reference_rejects_multiple_tracks(self, tmp_path):
        """--reference with more than one track is an error and runs no work."""
        mka1 = tmp_path / "a.mka"
        mka1.write_bytes(b"fake")
        mka2 = tmp_path / "b.mka"
        mka2.write_bytes(b"fake")
        ref = tmp_path / "ref.png"
        from PIL import Image

        Image.new("RGB", (32, 32), "blue").save(ref)

        from yoto_cli.commands.icons import handle_select_icon

        with (
            patch("yoto_cli.commands.icons.YotoAPI"),
            patch("yoto_lib.icons.select.select_icons_for_tracks") as mock_select,
            pytest.raises(SystemExit),
        ):
            handle_select_icon(argparse.Namespace(tracks=[mka1, mka2], reference=ref, strength=None))

        mock_select.assert_not_called()

    def test_select_icon_strength_requires_reference(self, tmp_path):
        """--strength without --reference is an error."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")

        from yoto_cli.commands.icons import handle_select_icon

        with (
            patch("yoto_cli.commands.icons.YotoAPI"),
            patch("yoto_lib.icons.select.select_icons_for_tracks") as mock_select,
            pytest.raises(SystemExit),
        ):
            handle_select_icon(argparse.Namespace(tracks=[mka], reference=None, strength=0.5))

        mock_select.assert_not_called()

    def test_select_icon_rejects_invalid_reference_image(self, tmp_path):
        """A reference path that is not a valid image is an error before any API work."""
        mka = tmp_path / "track.mka"
        mka.write_bytes(b"fake")
        ref = tmp_path / "notimage.png"
        ref.write_text("this is not an image")

        from yoto_cli.commands.icons import handle_select_icon

        with (
            patch("yoto_cli.commands.icons.YotoAPI"),
            patch("yoto_lib.icons.select.select_icons_for_tracks") as mock_select,
            pytest.raises(SystemExit),
        ):
            handle_select_icon(argparse.Namespace(tracks=[mka], reference=ref, strength=None))

        mock_select.assert_not_called()


class TestCopyIconCommand:
    def test_copy_icon_parses(self, tmp_path):
        parser = build_parser()
        args = parser.parse_args(
            ["copy-icon", str(tmp_path / "src.mka"), str(tmp_path / "a.mka"), str(tmp_path / "b.mka")]
        )
        assert args.command == "copy-icon"
        assert hasattr(args, "func")
        assert args.source == tmp_path / "src.mka"
        assert args.dests == [tmp_path / "a.mka", tmp_path / "b.mka"]

    def test_copy_icon_copies_to_multiple_dests(self, tmp_path):
        """Reads the source icon once and writes it to every .mka destination."""
        src = tmp_path / "src.mka"
        src.write_bytes(b"fake")
        dest1 = tmp_path / "d1.mka"
        dest1.write_bytes(b"fake")
        dest2 = tmp_path / "d2.mka"
        dest2.write_bytes(b"fake")

        icon_png = _png_bytes()

        from yoto_cli.commands.icons import handle_copy_icon

        with (
            patch("yoto_cli.commands.icons.get_attachment", return_value=icon_png) as mock_get,
            patch("yoto_cli.commands.icons.set_attachment") as mock_set,
            patch("yoto_lib.icons.set_macos_file_icon") as mock_macos,
        ):
            handle_copy_icon(argparse.Namespace(source=src, dests=[dest1, dest2]))

        mock_get.assert_called_once_with(src, "icon")
        set_targets = {call.args[0] for call in mock_set.call_args_list}
        assert set_targets == {dest1, dest2}
        for call in mock_set.call_args_list:
            assert call.args[2] == "icon"
            assert call.args[3] == "image/png"
        macos_targets = {call.args[0] for call in mock_macos.call_args_list}
        assert macos_targets == {dest1, dest2}

    def test_copy_icon_rejects_non_mka_source(self, tmp_path):
        """A non-.mka source errors cleanly without invoking mkvtoolnix."""
        txt = tmp_path / "description.txt"
        txt.write_text("not audio")
        dest = tmp_path / "d.mka"
        dest.write_bytes(b"fake")

        from yoto_cli.commands.icons import handle_copy_icon

        with (
            patch("yoto_cli.commands.icons.get_attachment") as mock_get,
            patch("yoto_cli.commands.icons.set_attachment") as mock_set,
            pytest.raises(SystemExit),
        ):
            handle_copy_icon(argparse.Namespace(source=txt, dests=[dest]))

        mock_get.assert_not_called()
        mock_set.assert_not_called()

    def test_copy_icon_errors_when_source_has_no_icon(self, tmp_path):
        """When the source lacks an icon, exit and touch no destinations."""
        src = tmp_path / "src.mka"
        src.write_bytes(b"fake")
        dest = tmp_path / "d.mka"
        dest.write_bytes(b"fake")

        from yoto_cli.commands.icons import handle_copy_icon

        with (
            patch("yoto_cli.commands.icons.get_attachment", return_value=None),
            patch("yoto_cli.commands.icons.set_attachment") as mock_set,
            pytest.raises(SystemExit),
        ):
            handle_copy_icon(argparse.Namespace(source=src, dests=[dest]))

        mock_set.assert_not_called()

    def test_copy_icon_drops_non_mka_dests(self, tmp_path):
        """Non-.mka destinations from a glob are silently ignored."""
        src = tmp_path / "src.mka"
        src.write_bytes(b"fake")
        dest = tmp_path / "d.mka"
        dest.write_bytes(b"fake")
        txt = tmp_path / "description.txt"
        txt.write_text("not audio")

        from yoto_cli.commands.icons import handle_copy_icon

        with (
            patch("yoto_cli.commands.icons.get_attachment", return_value=_png_bytes()),
            patch("yoto_cli.commands.icons.set_attachment") as mock_set,
            patch("yoto_lib.icons.set_macos_file_icon"),
        ):
            handle_copy_icon(argparse.Namespace(source=src, dests=[dest, txt]))

        set_targets = {call.args[0] for call in mock_set.call_args_list}
        assert set_targets == {dest}

    def test_copy_icon_continues_after_dest_error(self, tmp_path):
        """An error on one destination does not stop the others."""
        src = tmp_path / "src.mka"
        src.write_bytes(b"fake")
        dest1 = tmp_path / "d1.mka"
        dest1.write_bytes(b"fake")
        dest2 = tmp_path / "d2.mka"
        dest2.write_bytes(b"fake")

        from yoto_cli.commands.icons import handle_copy_icon

        def _set(path, *_args, **_kwargs):
            if path == dest1:
                raise OSError("mkvpropedit failed")

        with (
            patch("yoto_cli.commands.icons.get_attachment", return_value=_png_bytes()),
            patch("yoto_cli.commands.icons.set_attachment", side_effect=_set) as mock_set,
            patch("yoto_lib.icons.set_macos_file_icon"),
        ):
            handle_copy_icon(argparse.Namespace(source=src, dests=[dest1, dest2]))

        set_targets = {call.args[0] for call in mock_set.call_args_list}
        assert set_targets == {dest1, dest2}


# ── test_print_command ───────────────────────────────────────────────────────


class TestPrintCommand:
    def test_print_parses(self):
        parser = build_parser()
        args = parser.parse_args(["print", "/some/path"])
        assert args.command == "print"
        assert hasattr(args, "func")

    def test_print_sends_to_printer(self, tmp_path):
        """print calls print_cover on the playlist's cover.png."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        from yoto_cli.commands.cover import handle_print

        with (
            patch("yoto_cli.commands.cover.print_cover") as mock_print,
            patch("rich.prompt.Confirm.ask", return_value=True),
            patch.dict(os.environ, {}, clear=False) as env,
        ):
            env.pop("YOTO_ICC_PROFILE", None)
            handle_print(argparse.Namespace(path=folder, yes=False, profile=None))

        mock_print.assert_called_once()
        assert mock_print.call_args[0][0] == folder / "cover.png"
        assert mock_print.call_args[1]["icc_profile"] is None

    def test_print_yes_skips_confirm(self, tmp_path):
        """print --yes skips the confirmation prompt."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        from yoto_cli.commands.cover import handle_print

        with patch("yoto_cli.commands.cover.print_cover") as mock_print:
            handle_print(argparse.Namespace(path=folder, yes=True, profile=None))

        mock_print.assert_called_once()

    def test_print_no_cover_offers_generation(self, tmp_path):
        """print offers to generate cover when cover.png is missing."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        (folder / "description.txt").write_text("A test playlist")

        from yoto_cli.commands.cover import handle_print

        with (
            patch("yoto_cli.commands.cover.generate_cover_if_missing") as mock_gen,
            patch("yoto_cli.commands.cover.print_cover"),
            patch("rich.prompt.Confirm.ask", return_value=True),
            pytest.raises(SystemExit),
        ):
            handle_print(argparse.Namespace(path=folder, yes=False, profile=None))

        mock_gen.assert_called_once()

    def test_print_no_cover_decline_generation(self, tmp_path):
        """print exits cleanly when user declines cover generation."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)

        from yoto_cli.commands.cover import handle_print

        with (
            patch("yoto_cli.commands.cover.generate_cover_if_missing") as mock_gen,
            patch("rich.prompt.Confirm.ask", return_value=False),
        ):
            handle_print(argparse.Namespace(path=folder, yes=False, profile=None))

        mock_gen.assert_not_called()

    def test_print_with_profile(self, tmp_path):
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

        from yoto_cli.commands.cover import handle_print

        with patch("yoto_cli.commands.cover.print_cover") as mock_print:
            handle_print(argparse.Namespace(path=folder, yes=True, profile=fake_profile))

        mock_print.assert_called_once()
        assert mock_print.call_args[1]["icc_profile"] == str(fake_profile)

    def test_print_missing_profile_warns(self, tmp_path):
        """print warns and offers to continue when profile not found."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        from yoto_cli.commands.cover import handle_print

        with (
            patch("yoto_cli.commands.cover.print_cover") as mock_print,
            patch("rich.prompt.Confirm.ask", side_effect=[True, True]),
        ):
            handle_print(argparse.Namespace(path=folder, yes=False, profile=Path("/nonexistent.icc")))

        mock_print.assert_called_once()
        assert mock_print.call_args[1]["icc_profile"] is None

    def test_print_error_shows_message(self, tmp_path):
        """PrintError is surfaced as SystemExit."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "playlist.jsonl").write_text('"track.mka"\n')
        (folder / "track.mka").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        from yoto_cli.commands.cover import handle_print

        with (
            patch("yoto_cli.commands.cover.print_cover", side_effect=PrintError("Printer offline")),
            pytest.raises(SystemExit),
        ):
            handle_print(argparse.Namespace(path=folder, yes=True, profile=None))


# ── test_sync_print ──────────────────────────────────────────────────────────


class TestSyncPrint:
    def test_sync_print_flag(self, tmp_path):
        """sync --print calls print_cover when cover was uploaded."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=True, folder=folder)]

        from yoto_cli.commands.sync import handle_sync

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results),
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=False,
                    no_trim=False,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=True,
                )
            )

        mock_print.assert_called_once()
        assert mock_print.call_args[0][0] == folder / "cover.png"

    def test_sync_no_print_flag(self, tmp_path):
        """sync --no-print skips printing even when cover was uploaded."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=True, folder=folder)]

        from yoto_cli.commands.sync import handle_sync

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results),
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=False,
                    no_trim=False,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=False,
                )
            )

        mock_print.assert_not_called()

    def test_sync_prompts_when_cover_uploaded(self, tmp_path):
        """sync prompts to print when cover was uploaded and no flag given."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=True, folder=folder)]

        from yoto_cli.commands.sync import handle_sync

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results),
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
            patch("rich.prompt.Confirm.ask", return_value=True),
        ):
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=False,
                    no_trim=False,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=None,
                )
            )

        mock_print.assert_called_once()

    def test_sync_no_prompt_when_cover_not_uploaded(self, tmp_path):
        """sync does not prompt to print when cover was not uploaded."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=False, folder=folder)]

        from yoto_cli.commands.sync import handle_sync

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results),
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=False,
                    no_trim=False,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=None,
                )
            )

        mock_print.assert_not_called()

    def test_sync_dry_run_never_prints(self, tmp_path):
        """sync --dry-run never prints, regardless of --print flag."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)

        fake_results = [SyncResult(card_id=None, tracks_uploaded=2, dry_run=True, cover_uploaded=True, folder=folder)]

        from yoto_cli.commands.sync import handle_sync

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results),
            patch("yoto_cli.commands.sync.print_cover") as mock_print,
        ):
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=True,
                    no_trim=False,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=True,
                )
            )

        mock_print.assert_not_called()

    def test_sync_print_error_shown_as_warning(self, tmp_path):
        """If printing fails during sync, show a warning but don't fail the sync."""
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "track01.mp3").write_bytes(b"\x00" * 16)
        from PIL import Image

        img = Image.new("RGB", (638, 1011), "blue")
        img.save(folder / "cover.png")

        fake_results = [SyncResult(card_id="CARD-001", tracks_uploaded=1, cover_uploaded=True, folder=folder)]

        from yoto_cli.commands.sync import handle_sync

        with (
            patch("yoto_cli.commands.sync.sync_path", return_value=fake_results),
            patch("yoto_cli.commands.sync.print_cover", side_effect=PrintError("Printer offline")),
        ):
            handle_sync(
                argparse.Namespace(
                    path=folder,
                    dry_run=False,
                    no_trim=False,
                    ignore_album_art=False,
                    force_cover=False,
                    print_cover_flag=True,
                )
            )
