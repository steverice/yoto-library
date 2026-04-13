"""Integration smoke tests — full CLI → library workflow with real filesystem."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from yoto_cli.main import cli
from yoto_lib.playlist import read_jsonl


class TestEndToEnd:
    # ── test_init_add_files_sync ──────────────────────────────────────────────

    def test_init_add_files_sync(self, tmp_path):
        """Full workflow: init → add files → sync writes card_id and playlist."""
        runner = CliRunner()
        playlist_dir = tmp_path / "my-album"

        # 1. Init the playlist folder
        from yoto_cli.commands.misc import handle_init

        handle_init(argparse.Namespace(path=playlist_dir))
        assert playlist_dir.exists()

        # 2. Write fake MKA files and description
        (playlist_dir / "song-one.mka").write_bytes(b"\x00" * 64)
        (playlist_dir / "song-two.mka").write_bytes(b"\x00" * 64)
        (playlist_dir / "description.txt").write_text("A great album", encoding="utf-8")

        # Update playlist.jsonl to reference the new files (simulates yoto import)
        jsonl_path = playlist_dir / "playlist.jsonl"
        jsonl_path.write_text('"song-one.mka"\n"song-two.mka"\n', encoding="utf-8")

        # 3. Mock the API and helpers
        mock_api = MagicMock()
        mock_api.get_content.side_effect = Exception("no remote card yet")
        mock_api.upload_and_transcode.return_value = {"transcodedSha256": "deadbeef01"}
        mock_api.create_or_update_content.return_value = {"cardId": "SMOKE-001"}

        with (
            patch("yoto_lib.sync.YotoAPI", return_value=mock_api),
            patch("yoto_lib.sync.resolve_icons", return_value={}),
            patch("yoto_lib.sync.generate_cover_if_missing"),
        ):
            # 4. Run sync
            result = runner.invoke(cli, ["sync", str(playlist_dir)])

        # 5. Verify
        assert result.exit_code == 0, result.output
        assert "SMOKE-001" in result.output

        card_id_file = playlist_dir / ".yoto-card-id"
        assert card_id_file.exists(), ".yoto-card-id was not written"
        assert card_id_file.read_text(encoding="utf-8").strip() == "SMOKE-001"

        jsonl_path = playlist_dir / "playlist.jsonl"
        tracks = read_jsonl(jsonl_path)
        assert "song-one.mka" in tracks
        assert "song-two.mka" in tracks
        assert len(tracks) == 2

    # ── test_reorder_changes_track_order ──────────────────────────────────────

    def test_reorder_changes_track_order(self, tmp_path):
        """reorder command with mocked editor writes reversed track order."""
        playlist_dir = tmp_path / "reorder-album"
        playlist_dir.mkdir()

        # 1. Create MKA stubs and playlist.jsonl with [a, b, c]
        for name in ("a.mka", "b.mka", "c.mka"):
            (playlist_dir / name).write_bytes(b"\x00" * 16)

        jsonl_path = playlist_dir / "playlist.jsonl"
        original_lines = '"a.mka"\n"b.mka"\n"c.mka"\n'
        jsonl_path.write_text(original_lines, encoding="utf-8")

        # 2. Mock _open_editor to return reversed order
        reversed_content = '"c.mka"\n"b.mka"\n"a.mka"\n'

        from yoto_cli.commands.misc import handle_reorder

        with patch("yoto_cli.main._open_editor", return_value=reversed_content):
            # 3. Run reorder
            handle_reorder(argparse.Namespace(playlist=jsonl_path))

        # 4. Verify
        new_tracks = read_jsonl(jsonl_path)
        assert new_tracks == ["c.mka", "b.mka", "a.mka"]
