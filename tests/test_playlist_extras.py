"""Tests for playlist edge cases and helper functions."""

from __future__ import annotations

import hashlib

from yoto_lib.playlist import (
    Playlist,
    _cover_hash,
    _title_from_filename,
    diff_playlists,
    read_jsonl,
    write_jsonl,
)


class TestTitleFromFilename:
    def test_strips_mka_extension(self):
        assert _title_from_filename("My Song.mka") == "My Song"

    def test_strips_mp3_extension(self):
        assert _title_from_filename("track01.mp3") == "track01"

    def test_handles_dots_in_stem(self):
        assert _title_from_filename("Dr. Seuss.mka") == "Dr. Seuss"

    def test_no_extension(self):
        assert _title_from_filename("noext") == "noext"


class TestCoverHash:
    def test_returns_sha256_of_file(self, tmp_path):
        cover = tmp_path / "cover.png"
        cover.write_bytes(b"\x89PNG cover data")
        expected = hashlib.sha256(b"\x89PNG cover data").hexdigest()
        assert _cover_hash(cover) == expected


class TestWriteJsonlEdgeCases:
    def test_empty_list_produces_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        write_jsonl(f, [])
        assert f.read_text() == ""

    def test_single_item(self, tmp_path):
        f = tmp_path / "single.jsonl"
        write_jsonl(f, ["track.mka"])
        assert f.read_text() == '"track.mka"\n'

    def test_special_characters_in_filenames(self, tmp_path):
        f = tmp_path / "special.jsonl"
        names = ['track "one".mka', "track\ttwo.mka"]
        write_jsonl(f, names)
        result = read_jsonl(f)
        assert result == names


class TestDiffPlaylistsCoverHash:
    def test_cover_changed_when_hash_differs(self, tmp_path):
        """Cover is marked changed when local file hash differs from stored hash."""
        folder = tmp_path / "album"
        folder.mkdir()
        cover = folder / "cover.png"
        cover.write_bytes(b"new cover content")
        hash_file = folder / ".yoto-cover-hash"
        hash_file.write_text("old_hash_value")

        pl = Playlist(
            path=folder,
            title="album",
            track_files=["t.mka"],
            card_id=None,
            description=None,
            has_cover=True,
            missing_files=[],
        )
        remote = {"tracks": ["t"], "has_cover": True}
        diff = diff_playlists(pl, remote)
        assert diff.cover_changed is True

    def test_cover_unchanged_when_hash_matches(self, tmp_path):
        """Cover is NOT marked changed when local hash matches stored hash."""
        folder = tmp_path / "album"
        folder.mkdir()
        cover = folder / "cover.png"
        cover.write_bytes(b"same cover content")
        correct_hash = hashlib.sha256(b"same cover content").hexdigest()
        hash_file = folder / ".yoto-cover-hash"
        hash_file.write_text(correct_hash)

        pl = Playlist(
            path=folder,
            title="album",
            track_files=["t.mka"],
            card_id=None,
            description=None,
            has_cover=True,
            missing_files=[],
        )
        remote = {"tracks": ["t"], "has_cover": True}
        diff = diff_playlists(pl, remote)
        assert diff.cover_changed is False

    def test_cover_not_changed_when_both_absent(self, tmp_path):
        """No cover locally and no cover remotely means not changed."""
        folder = tmp_path / "album"
        folder.mkdir()
        pl = Playlist(
            path=folder,
            title="album",
            track_files=[],
            card_id=None,
            description=None,
            has_cover=False,
            missing_files=[],
        )
        remote = {"tracks": [], "has_cover": False}
        diff = diff_playlists(pl, remote)
        assert diff.cover_changed is False


class TestDiffPlaylistsDescriptionChanged:
    def test_description_changed_from_none_to_text(self, tmp_path):
        pl = Playlist(
            path=tmp_path,
            title="t",
            track_files=[],
            card_id=None,
            description="new desc",
            has_cover=False,
            missing_files=[],
        )
        remote = {"tracks": [], "description": None}
        diff = diff_playlists(pl, remote)
        assert diff.metadata_changed is True

    def test_description_unchanged(self, tmp_path):
        pl = Playlist(
            path=tmp_path,
            title="t",
            track_files=[],
            card_id=None,
            description="same",
            has_cover=False,
            missing_files=[],
        )
        remote = {"tracks": [], "description": "same"}
        diff = diff_playlists(pl, remote)
        assert diff.metadata_changed is False


class TestPlaylistProperties:
    def test_cover_path(self, tmp_path):
        pl = Playlist(
            path=tmp_path,
            title="t",
            track_files=[],
            card_id=None,
            description=None,
            has_cover=False,
            missing_files=[],
        )
        assert pl.cover_path == tmp_path / "cover.png"

    def test_description_path(self, tmp_path):
        pl = Playlist(
            path=tmp_path,
            title="t",
            track_files=[],
            card_id=None,
            description=None,
            has_cover=False,
            missing_files=[],
        )
        assert pl.description_path == tmp_path / "description.txt"

    def test_jsonl_path(self, tmp_path):
        pl = Playlist(
            path=tmp_path,
            title="t",
            track_files=[],
            card_id=None,
            description=None,
            has_cover=False,
            missing_files=[],
        )
        assert pl.jsonl_path == tmp_path / "playlist.jsonl"

    def test_card_id_path(self, tmp_path):
        pl = Playlist(
            path=tmp_path,
            title="t",
            track_files=[],
            card_id=None,
            description=None,
            has_cover=False,
            missing_files=[],
        )
        assert pl.card_id_path == tmp_path / ".yoto-card-id"

    def test_cover_hash_path(self, tmp_path):
        pl = Playlist(
            path=tmp_path,
            title="t",
            track_files=[],
            card_id=None,
            description=None,
            has_cover=False,
            missing_files=[],
        )
        assert pl.cover_hash_path == tmp_path / ".yoto-cover-hash"
