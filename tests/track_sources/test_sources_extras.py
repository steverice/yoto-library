"""Tests for sources edge cases — _unique_path, parse_webloc error handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from yoto_lib.track_sources import _unique_path, parse_webloc


class TestUniquePath:
    def test_no_collision(self, tmp_path):
        """Returns stem.suffix when no file exists."""
        result = _unique_path(tmp_path, "song", ".mka")
        assert result == tmp_path / "song.mka"

    def test_one_collision(self, tmp_path):
        """Appends ' 2' when the base name is taken."""
        (tmp_path / "song.mka").write_bytes(b"")
        result = _unique_path(tmp_path, "song", ".mka")
        assert result == tmp_path / "song 2.mka"

    def test_multiple_collisions(self, tmp_path):
        """Increments suffix until a free name is found."""
        (tmp_path / "song.mka").write_bytes(b"")
        (tmp_path / "song 2.mka").write_bytes(b"")
        (tmp_path / "song 3.mka").write_bytes(b"")
        result = _unique_path(tmp_path, "song", ".mka")
        assert result == tmp_path / "song 4.mka"


class TestParseWeblocErrors:
    def test_nonexistent_file_returns_none(self, tmp_path):
        """parse_webloc returns None for a missing file."""
        result = parse_webloc(tmp_path / "missing.webloc")
        assert result is None

    def test_binary_garbage_returns_none(self, tmp_path):
        """parse_webloc returns None for binary garbage."""
        f = tmp_path / "garbage.webloc"
        f.write_bytes(b"\xff\xfe\x00\x01" * 100)
        assert parse_webloc(f) is None
