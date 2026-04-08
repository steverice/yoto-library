"""Tests for lyrics fetch pipeline."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import httpx
import pytest


class TestReadLyricsFromTags:
    def test_returns_lyrics_when_present(self):
        from yoto_lib.lyrics import read_lyrics_from_tags
        tags = {"title": "Old MacDonald", "artist": "Kids", "lyrics": "Old MacDonald had a farm"}
        assert read_lyrics_from_tags(tags) == "Old MacDonald had a farm"

    def test_returns_none_when_absent(self):
        from yoto_lib.lyrics import read_lyrics_from_tags
        tags = {"title": "Old MacDonald", "artist": "Kids"}
        assert read_lyrics_from_tags(tags) is None

    def test_returns_none_for_empty_lyrics(self):
        from yoto_lib.lyrics import read_lyrics_from_tags
        tags = {"title": "Old MacDonald", "lyrics": ""}
        assert read_lyrics_from_tags(tags) is None

    def test_returns_none_for_whitespace_only(self):
        from yoto_lib.lyrics import read_lyrics_from_tags
        tags = {"title": "Old MacDonald", "lyrics": "   \n  "}
        assert read_lyrics_from_tags(tags) is None


class TestFetchLyricsLrclib:
    def test_returns_plain_lyrics_on_match(self):
        from yoto_lib.lyrics import fetch_lyrics_lrclib
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "trackName": "Old MacDonald Had a Farm",
                "artistName": "Kids Songs",
                "plainLyrics": "Old MacDonald had a farm, E-I-E-I-O",
                "syncedLyrics": "[00:00.00] Old MacDonald had a farm",
            }
        ]
        mock_response.raise_for_status = MagicMock()

        with patch("yoto_lib.lyrics.httpx.get", return_value=mock_response):
            result = fetch_lyrics_lrclib("Kids Songs", "Old MacDonald Had a Farm")

        assert result == "Old MacDonald had a farm, E-I-E-I-O"

    def test_returns_none_on_empty_results(self):
        from yoto_lib.lyrics import fetch_lyrics_lrclib
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch("yoto_lib.lyrics.httpx.get", return_value=mock_response):
            result = fetch_lyrics_lrclib("Unknown", "Nonexistent Song")

        assert result is None

    def test_returns_none_on_network_error(self):
        from yoto_lib.lyrics import fetch_lyrics_lrclib
        with patch("yoto_lib.lyrics.httpx.get", side_effect=httpx.HTTPError("network")):
            result = fetch_lyrics_lrclib("Artist", "Title")

        assert result is None

    def test_prefers_plain_over_synced(self):
        from yoto_lib.lyrics import fetch_lyrics_lrclib
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "trackName": "Song",
                "artistName": "Artist",
                "plainLyrics": None,
                "syncedLyrics": "[00:00.00] Synced only lyrics here",
            }
        ]
        mock_response.raise_for_status = MagicMock()

        with patch("yoto_lib.lyrics.httpx.get", return_value=mock_response):
            result = fetch_lyrics_lrclib("Artist", "Song")

        assert result is not None
        assert "Synced only lyrics here" in result

    def test_sends_user_agent(self):
        from yoto_lib.lyrics import fetch_lyrics_lrclib, _USER_AGENT
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch("yoto_lib.lyrics.httpx.get", return_value=mock_response) as mock_get:
            fetch_lyrics_lrclib("Artist", "Title")

        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["User-Agent"] == _USER_AGENT


class TestGetLyrics:
    def test_returns_lyrics_from_tags(self):
        from yoto_lib.lyrics import get_lyrics
        tags = {"title": "Song", "artist": "Artist", "lyrics": "La la la"}

        with patch("yoto_lib.lyrics.fetch_lyrics_lrclib") as mock_fetch:
            text, source = get_lyrics(tags)

        assert text == "La la la"
        assert source == "tags"
        mock_fetch.assert_not_called()

    def test_falls_back_to_lrclib(self):
        from yoto_lib.lyrics import get_lyrics
        tags = {"title": "Song", "artist": "Artist"}

        with patch("yoto_lib.lyrics.fetch_lyrics_lrclib", return_value="API lyrics") as mock_fetch:
            text, source = get_lyrics(tags)

        assert text == "API lyrics"
        assert source == "lrclib"
        mock_fetch.assert_called_once_with("Artist", "Song")

    def test_returns_none_when_both_fail(self):
        from yoto_lib.lyrics import get_lyrics
        tags = {"title": "Song", "artist": "Artist"}

        with patch("yoto_lib.lyrics.fetch_lyrics_lrclib", return_value=None):
            text, source = get_lyrics(tags)

        assert text is None
        assert source == "none"

    def test_skips_lrclib_when_no_artist(self):
        from yoto_lib.lyrics import get_lyrics
        tags = {"title": "Song"}

        with patch("yoto_lib.lyrics.fetch_lyrics_lrclib") as mock_fetch:
            text, source = get_lyrics(tags)

        assert text is None
        assert source == "none"
        mock_fetch.assert_not_called()

    def test_skips_lrclib_when_no_title(self):
        from yoto_lib.lyrics import get_lyrics
        tags = {"artist": "Artist"}

        with patch("yoto_lib.lyrics.fetch_lyrics_lrclib") as mock_fetch:
            text, source = get_lyrics(tags)

        assert text is None
        assert source == "none"
        mock_fetch.assert_not_called()
