"""Tests for yoto_lib.sources — URL source resolution."""

from __future__ import annotations

import plistlib
from pathlib import Path

from yoto_lib.sources import parse_webloc


class TestParseWebloc:
    def test_parse_webloc_extracts_url(self, tmp_path):
        """parse_webloc reads a .webloc plist and returns the URL string."""
        url = "https://www.youtube.com/watch?v=GxtknJ9KFKY"
        webloc = tmp_path / "song.webloc"
        webloc.write_bytes(plistlib.dumps({"URL": url}))

        assert parse_webloc(webloc) == url

    def test_parse_webloc_missing_url_key(self, tmp_path):
        """parse_webloc returns None when the plist has no URL key."""
        webloc = tmp_path / "bad.webloc"
        webloc.write_bytes(plistlib.dumps({"Name": "something"}))

        assert parse_webloc(webloc) is None

    def test_parse_webloc_invalid_plist(self, tmp_path):
        """parse_webloc returns None for a corrupt file."""
        webloc = tmp_path / "corrupt.webloc"
        webloc.write_bytes(b"this is not a plist")

        assert parse_webloc(webloc) is None
