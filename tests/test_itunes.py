from unittest.mock import patch, MagicMock
import pytest

from yoto_lib.itunes import search_itunes_album, match_album, _artwork_url


class TestSearchItunesAlbum:
    def test_returns_parsed_results(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "resultCount": 1,
            "results": [
                {
                    "collectionName": "Life's Little Lessons",
                    "artistName": "Daniel Tiger",
                    "artworkUrl100": "https://example.com/art/100x100bb.jpg",
                    "primaryGenreName": "Children's Music",
                    "releaseDate": "2012-12-10T08:00:00Z",
                    "copyright": "2012 Fred Rogers",
                }
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("yoto_lib.itunes.httpx.get", return_value=mock_response) as mock_get:
            results = search_itunes_album("Daniel Tiger", "Life's Little Lessons")

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert "Daniel Tiger" in kwargs["params"]["term"]
        assert kwargs["params"]["entity"] == "album"
        assert len(results) == 1
        assert results[0]["collectionName"] == "Life's Little Lessons"

    def test_returns_empty_on_network_error(self):
        with patch("yoto_lib.itunes.httpx.get", side_effect=Exception("network")):
            results = search_itunes_album("Artist", "Album")
        assert results == []


class TestMatchAlbum:
    def test_exact_match(self):
        results = [
            {"collectionName": "Life's Little Lessons", "artistName": "Daniel Tiger"},
        ]
        match = match_album(results, "Daniel Tiger", "Life's Little Lessons")
        assert match is not None
        assert match["collectionName"] == "Life's Little Lessons"

    def test_fuzzy_match_with_prefix(self):
        results = [
            {
                "collectionName": "Daniel Tiger's Neighborhood: Life's Little Lessons",
                "artistName": "Daniel Tiger",
            },
        ]
        match = match_album(results, "Daniel Tiger", "Life's Little Lessons")
        assert match is not None

    def test_no_match_below_threshold(self):
        results = [
            {"collectionName": "Completely Different Album", "artistName": "Other Artist"},
        ]
        match = match_album(results, "Daniel Tiger", "Life's Little Lessons")
        assert match is None

    def test_picks_best_from_multiple(self):
        results = [
            {"collectionName": "Wrong Album", "artistName": "Daniel Tiger"},
            {"collectionName": "Life's Little Lessons", "artistName": "Daniel Tiger"},
        ]
        match = match_album(results, "Daniel Tiger", "Life's Little Lessons")
        assert match is not None
        assert match["collectionName"] == "Life's Little Lessons"

    def test_empty_results(self):
        assert match_album([], "Artist", "Album") is None


class TestArtworkUrl:
    def test_rewrites_size(self):
        url = "https://is1-ssl.mzstatic.com/image/thumb/Music125/v4/ab/cd/ef/img.jpg/100x100bb.jpg"
        result = _artwork_url(url, 1200)
        assert result == "https://is1-ssl.mzstatic.com/image/thumb/Music125/v4/ab/cd/ef/img.jpg/1200x1200bb.jpg"

    def test_rewrites_60px(self):
        url = "https://is1-ssl.mzstatic.com/image/thumb/Music125/v4/ab/cd/ef/img.jpg/60x60bb.jpg"
        result = _artwork_url(url, 600)
        assert result == "https://is1-ssl.mzstatic.com/image/thumb/Music125/v4/ab/cd/ef/img.jpg/600x600bb.jpg"

    def test_handles_missing_pattern(self):
        url = "https://example.com/art.jpg"
        result = _artwork_url(url, 1200)
        assert result == url  # unchanged
