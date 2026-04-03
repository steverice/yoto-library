"""Tests for yoto_lib.playlist — playlist model bridging filesystem and Yoto API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yoto_lib.playlist import (
    AUDIO_EXTENSIONS,
    Playlist,
    PlaylistDiff,
    build_content_schema,
    diff_playlists,
    load_playlist,
    read_jsonl,
    scan_audio_files,
    write_jsonl,
)


# ── TestJsonl ─────────────────────────────────────────────────────────────────


class TestJsonl:
    def test_read_jsonl_basic(self, tmp_path):
        f = tmp_path / "playlist.jsonl"
        f.write_text('"track01.mka"\n"track02.mka"\n"track03.mka"\n')
        result = read_jsonl(f)
        assert result == ["track01.mka", "track02.mka", "track03.mka"]

    def test_read_jsonl_ignores_blank_lines(self, tmp_path):
        f = tmp_path / "playlist.jsonl"
        f.write_text('"track01.mka"\n\n"track02.mka"\n\n')
        result = read_jsonl(f)
        assert result == ["track01.mka", "track02.mka"]

    def test_write_jsonl(self, tmp_path):
        f = tmp_path / "playlist.jsonl"
        write_jsonl(f, ["alpha.mka", "beta.mp3"])
        lines = f.read_text().splitlines()
        assert lines == ['"alpha.mka"', '"beta.mp3"']

    def test_jsonl_roundtrip(self, tmp_path):
        f = tmp_path / "playlist.jsonl"
        original = ["track01.mka", "track02.mp3", "track03.flac"]
        write_jsonl(f, original)
        result = read_jsonl(f)
        assert result == original


# ── TestScanAudioFiles ────────────────────────────────────────────────────────


class TestScanAudioFiles:
    def test_finds_mka_files(self, tmp_path):
        (tmp_path / "a.mka").write_bytes(b"")
        (tmp_path / "b.mka").write_bytes(b"")
        result = scan_audio_files(tmp_path)
        assert [p.name for p in result] == ["a.mka", "b.mka"]

    def test_finds_all_audio_formats(self, tmp_path):
        for ext in [".mka", ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma"]:
            (tmp_path / f"track{ext}").write_bytes(b"")
        result = scan_audio_files(tmp_path)
        extensions_found = {p.suffix for p in result}
        assert extensions_found == AUDIO_EXTENSIONS

    def test_ignores_non_audio_files(self, tmp_path):
        (tmp_path / "cover.png").write_bytes(b"")
        (tmp_path / "description.txt").write_bytes(b"")
        (tmp_path / ".yoto-card-id").write_bytes(b"")
        (tmp_path / "track.mka").write_bytes(b"")
        result = scan_audio_files(tmp_path)
        assert [p.name for p in result] == ["track.mka"]


# ── TestLoadPlaylist ──────────────────────────────────────────────────────────


class TestLoadPlaylist:
    def test_load_with_existing_jsonl(self, tmp_path):
        folder = tmp_path / "My Album"
        folder.mkdir()
        (folder / "01.mka").write_bytes(b"")
        (folder / "02.mka").write_bytes(b"")
        write_jsonl(folder / "playlist.jsonl", ["01.mka", "02.mka"])

        pl = load_playlist(folder)
        assert pl.track_files == ["01.mka", "02.mka"]
        assert pl.title == "My Album"
        assert pl.missing_files == []

    def test_auto_generates_jsonl_when_absent(self, tmp_path):
        folder = tmp_path / "Auto Album"
        folder.mkdir()
        (folder / "b_track.mka").write_bytes(b"")
        (folder / "a_track.mka").write_bytes(b"")

        pl = load_playlist(folder)
        # alphabetical order
        assert pl.track_files == ["a_track.mka", "b_track.mka"]
        # jsonl is written to disk
        assert (folder / "playlist.jsonl").exists()
        assert read_jsonl(folder / "playlist.jsonl") == ["a_track.mka", "b_track.mka"]

    def test_appends_unlisted_files(self, tmp_path):
        folder = tmp_path / "Partial"
        folder.mkdir()
        (folder / "01.mka").write_bytes(b"")
        (folder / "02.mka").write_bytes(b"")
        (folder / "03.mka").write_bytes(b"")
        write_jsonl(folder / "playlist.jsonl", ["01.mka", "02.mka"])

        pl = load_playlist(folder)
        assert pl.track_files == ["01.mka", "02.mka", "03.mka"]

    def test_flags_missing_files(self, tmp_path):
        folder = tmp_path / "Missing"
        folder.mkdir()
        (folder / "01.mka").write_bytes(b"")
        write_jsonl(folder / "playlist.jsonl", ["01.mka", "ghost.mka"])

        pl = load_playlist(folder)
        assert "ghost.mka" in pl.missing_files
        assert "ghost.mka" not in pl.track_files

    def test_reads_card_id(self, tmp_path):
        folder = tmp_path / "WithCard"
        folder.mkdir()
        (folder / ".yoto-card-id").write_text("abc123\n")
        write_jsonl(folder / "playlist.jsonl", [])

        pl = load_playlist(folder)
        assert pl.card_id == "abc123"

    def test_card_id_none_when_absent(self, tmp_path):
        folder = tmp_path / "NoCard"
        folder.mkdir()
        write_jsonl(folder / "playlist.jsonl", [])

        pl = load_playlist(folder)
        assert pl.card_id is None

    def test_reads_description(self, tmp_path):
        folder = tmp_path / "WithDesc"
        folder.mkdir()
        (folder / "description.txt").write_text("A great album\n")
        write_jsonl(folder / "playlist.jsonl", [])

        pl = load_playlist(folder)
        assert pl.description == "A great album\n"

    def test_has_cover_true_and_false(self, tmp_path):
        folder_with = tmp_path / "WithCover"
        folder_with.mkdir()
        (folder_with / "cover.png").write_bytes(b"")
        write_jsonl(folder_with / "playlist.jsonl", [])

        folder_without = tmp_path / "NoCover"
        folder_without.mkdir()
        write_jsonl(folder_without / "playlist.jsonl", [])

        assert load_playlist(folder_with).has_cover is True
        assert load_playlist(folder_without).has_cover is False


# ── TestDiffPlaylists ─────────────────────────────────────────────────────────


def _make_playlist(folder, tracks, card_id=None, description=None, has_cover=False):
    return Playlist(
        path=folder,
        title=folder.name,
        track_files=tracks,
        card_id=card_id,
        description=description,
        has_cover=has_cover,
        missing_files=[],
    )


class TestDiffPlaylists:
    def test_diff_against_none_everything_new(self, tmp_path):
        pl = _make_playlist(tmp_path, ["01.mka", "02.mka"])
        diff = diff_playlists(pl, remote=None)
        assert diff.new_tracks == ["01.mka", "02.mka"]
        assert diff.removed_tracks == []
        assert diff.order_changed is False
        assert diff.cover_changed is True
        assert diff.metadata_changed is True

    def test_diff_new_track(self, tmp_path):
        pl = _make_playlist(tmp_path, ["01.mka", "02.mka", "03.mka"])
        remote = {"tracks": ["01.mka", "02.mka"]}
        diff = diff_playlists(pl, remote=remote)
        assert diff.new_tracks == ["03.mka"]
        assert diff.removed_tracks == []

    def test_diff_removed_track(self, tmp_path):
        pl = _make_playlist(tmp_path, ["01.mka"])
        remote = {"tracks": ["01.mka", "02.mka"]}
        diff = diff_playlists(pl, remote=remote)
        assert diff.removed_tracks == ["02.mka"]
        assert diff.new_tracks == []

    def test_diff_order_changed(self, tmp_path):
        pl = _make_playlist(tmp_path, ["02.mka", "01.mka"])
        remote = {"tracks": ["01.mka", "02.mka"]}
        diff = diff_playlists(pl, remote=remote)
        assert diff.order_changed is True
        assert diff.new_tracks == []
        assert diff.removed_tracks == []

    def test_diff_no_changes(self, tmp_path):
        pl = _make_playlist(
            tmp_path, ["01.mka", "02.mka"], description="same", has_cover=False
        )
        remote = {
            "tracks": ["01.mka", "02.mka"],
            "description": "same",
            "has_cover": False,
        }
        diff = diff_playlists(pl, remote=remote)
        assert diff.new_tracks == []
        assert diff.removed_tracks == []
        assert diff.order_changed is False
        assert diff.cover_changed is False
        assert diff.metadata_changed is False


# ── TestBuildContentSchema ────────────────────────────────────────────────────


class TestBuildContentSchema:
    def _make_basic_playlist(self, tmp_path):
        return Playlist(
            path=tmp_path / "My Story",
            title="My Story",
            track_files=["01 - Intro.mka", "02 - Main.mka"],
            card_id=None,
            description=None,
            has_cover=False,
            missing_files=[],
        )

    def test_basic_schema_structure(self, tmp_path):
        pl = self._make_basic_playlist(tmp_path)
        hashes = {"01 - Intro.mka": "abc123", "02 - Main.mka": "def456"}
        schema = build_content_schema(pl, track_hashes=hashes, icon_ids={}, cover_url=None)

        assert schema["content"]["title"] == "My Story"
        chapters = schema["content"]["chapters"]
        assert "01 - Intro.mka" in chapters
        assert "02 - Main.mka" in chapters
        assert chapters["01 - Intro.mka"]["tracks"][0]["trackUrl"] == "yoto:#abc123"
        assert chapters["02 - Main.mka"]["tracks"][0]["trackUrl"] == "yoto:#def456"

    def test_includes_card_id(self, tmp_path):
        pl = Playlist(
            path=tmp_path / "Tagged",
            title="Tagged",
            track_files=["track.mka"],
            card_id="CARD-XYZ",
            description=None,
            has_cover=False,
            missing_files=[],
        )
        hashes = {"track.mka": "aaa"}
        schema = build_content_schema(pl, track_hashes=hashes, icon_ids={}, cover_url=None)
        assert schema["cardId"] == "CARD-XYZ"

    def test_includes_icons(self, tmp_path):
        pl = Playlist(
            path=tmp_path / "Iconic",
            title="Iconic",
            track_files=["t1.mka", "t2.mka"],
            card_id=None,
            description=None,
            has_cover=False,
            missing_files=[],
        )
        hashes = {"t1.mka": "h1", "t2.mka": "h2"}
        icon_ids = {"t1.mka": "icon-001", "t2.mka": "icon-002"}
        schema = build_content_schema(pl, track_hashes=hashes, icon_ids=icon_ids, cover_url=None)
        chapters = schema["content"]["chapters"]
        assert chapters["t1.mka"]["display"]["icon16x16"] == "icon-001"
        assert chapters["t2.mka"]["display"]["icon16x16"] == "icon-002"

    def test_includes_cover_url(self, tmp_path):
        pl = self._make_basic_playlist(tmp_path)
        hashes = {"01 - Intro.mka": "abc", "02 - Main.mka": "def"}
        schema = build_content_schema(
            pl,
            track_hashes=hashes,
            icon_ids={},
            cover_url="https://cdn.yoto.io/cover.png",
        )
        assert schema["content"]["metadata"]["coverImage"] == "https://cdn.yoto.io/cover.png"
