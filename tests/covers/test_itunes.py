import os
import subprocess
import struct
from pathlib import Path
from unittest.mock import patch, MagicMock, call
import httpx
import pytest
from click.testing import CliRunner

from yoto_lib.covers.itunes import search_itunes_album, match_album, _artwork_url, embed_album_art, enrich_from_itunes
from yoto_lib.mka import wrap_in_mka, extract_album_art


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

        with patch("yoto_lib.covers.itunes.httpx.get", return_value=mock_response) as mock_get:
            results = search_itunes_album("Daniel Tiger", "Life's Little Lessons")

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert "Daniel Tiger" in kwargs["params"]["term"]
        assert kwargs["params"]["entity"] == "album"
        assert len(results) == 1
        assert results[0]["collectionName"] == "Life's Little Lessons"

    def test_returns_empty_on_network_error(self):
        with patch("yoto_lib.covers.itunes.httpx.get", side_effect=httpx.HTTPError("network")):
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


def ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


@needs_ffmpeg
class TestEmbedAlbumArt:
    def _make_wav(self, tmp_path: Path) -> Path:
        wav_path = tmp_path / "silence.wav"
        sample_rate = 44100
        num_channels = 1
        bits_per_sample = 16
        data_size = 2
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16, 1,
            num_channels, sample_rate,
            sample_rate * num_channels * bits_per_sample // 8,
            num_channels * bits_per_sample // 8, bits_per_sample,
            b"data", data_size,
        )
        wav_path.write_bytes(header + b"\x00\x00")
        return wav_path

    def _make_test_jpeg(self) -> bytes:
        """Create a minimal 200x200 JPEG via ffmpeg."""
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             "color=c=red:s=200x200:d=1", "-frames:v", "1",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, check=True,
        )
        return result.stdout

    def test_embeds_art_readable_by_extract(self, tmp_path):
        wav = self._make_wav(tmp_path)
        mka = tmp_path / "track.mka"
        wrap_in_mka(wav, mka)

        assert extract_album_art(mka) is None

        jpeg_bytes = self._make_test_jpeg()
        embed_album_art(mka, jpeg_bytes)

        art = extract_album_art(mka)
        assert art is not None
        assert len(art) > 100

    def test_returns_false_on_invalid_image(self, tmp_path):
        wav = self._make_wav(tmp_path)
        mka = tmp_path / "track.mka"
        wrap_in_mka(wav, mka)

        result = embed_album_art(mka, b"not an image")
        assert result is False


class TestEnrichFromItunes:
    def test_skips_when_art_already_exists(self, tmp_path):
        mka = tmp_path / "track.mka"
        mka.touch()
        cache = {}

        with patch("yoto_lib.covers.itunes.extract_album_art", return_value=b"existing art"):
            enrich_from_itunes(mka, {"artist": "A", "album": "B"}, cache)

        # Should not have queried the API
        assert cache == {}

    def test_skips_when_missing_artist_or_album(self, tmp_path):
        mka = tmp_path / "track.mka"
        mka.touch()
        cache = {}

        with patch("yoto_lib.covers.itunes.extract_album_art", return_value=None):
            enrich_from_itunes(mka, {"artist": "A"}, cache)  # no album
            enrich_from_itunes(mka, {"album": "B"}, cache)   # no artist
            enrich_from_itunes(mka, {}, cache)                # neither

        assert cache == {}

    def test_queries_api_and_embeds_art(self, tmp_path):
        mka = tmp_path / "track.mka"
        mka.touch()
        cache = {}

        api_result = {
            "collectionName": "Album",
            "artistName": "Artist",
            "artworkUrl100": "https://example.com/100x100bb.jpg",
            "primaryGenreName": "Rock",
            "releaseDate": "2020-01-01T00:00:00Z",
            "copyright": "2020 Label",
        }

        with (
            patch("yoto_lib.covers.itunes.extract_album_art", return_value=None),
            patch("yoto_lib.covers.itunes.search_itunes_album", return_value=[api_result]),
            patch("yoto_lib.covers.itunes.match_album", return_value=api_result),
            patch("yoto_lib.covers.itunes.httpx.get") as mock_get,
            patch("yoto_lib.covers.itunes.embed_album_art", return_value=True) as mock_embed,
            patch("yoto_lib.covers.itunes.write_tags") as mock_write_tags,
        ):
            mock_response = MagicMock()
            mock_response.content = b"fake jpeg bytes"
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            enrich_from_itunes(mka, {"artist": "Artist", "album": "Album"}, cache)

        mock_embed.assert_called_once_with(mka, b"fake jpeg bytes")

    def test_uses_cache_on_second_call(self, tmp_path):
        mka1 = tmp_path / "track1.mka"
        mka2 = tmp_path / "track2.mka"
        mka1.touch()
        mka2.touch()
        cache = {}

        api_result = {
            "collectionName": "Album",
            "artistName": "Artist",
            "artworkUrl100": "https://example.com/100x100bb.jpg",
        }

        mock_response = MagicMock()
        mock_response.content = b"jpeg"
        mock_response.raise_for_status = MagicMock()

        with (
            patch("yoto_lib.covers.itunes.extract_album_art", return_value=None),
            patch("yoto_lib.covers.itunes.search_itunes_album", return_value=[api_result]) as mock_search,
            patch("yoto_lib.covers.itunes.match_album", return_value=api_result),
            patch("yoto_lib.covers.itunes.httpx.get", return_value=mock_response) as mock_get,
            patch("yoto_lib.covers.itunes.embed_album_art", return_value=True),
            patch("yoto_lib.covers.itunes.write_tags"),
        ):
            enrich_from_itunes(mka1, {"artist": "Artist", "album": "Album"}, cache)
            enrich_from_itunes(mka2, {"artist": "Artist", "album": "Album"}, cache)

        # API search and artwork download should each happen only once
        mock_search.assert_called_once()
        mock_get.assert_called_once()

    def test_caches_no_match(self, tmp_path):
        mka1 = tmp_path / "track1.mka"
        mka2 = tmp_path / "track2.mka"
        mka1.touch()
        mka2.touch()
        cache = {}

        with (
            patch("yoto_lib.covers.itunes.extract_album_art", return_value=None),
            patch("yoto_lib.covers.itunes.search_itunes_album", return_value=[]) as mock_search,
        ):
            enrich_from_itunes(mka1, {"artist": "Artist", "album": "Album"}, cache)
            enrich_from_itunes(mka2, {"artist": "Artist", "album": "Album"}, cache)

        mock_search.assert_called_once()

    def test_backfills_missing_metadata(self, tmp_path):
        mka = tmp_path / "track.mka"
        mka.touch()
        cache = {}

        api_result = {
            "collectionName": "Album",
            "artistName": "Artist",
            "artworkUrl100": "https://example.com/100x100bb.jpg",
            "primaryGenreName": "Rock",
            "releaseDate": "2020-01-01T00:00:00Z",
            "copyright": "2020 Label",
        }

        mock_response = MagicMock()
        mock_response.content = b"jpeg"
        mock_response.raise_for_status = MagicMock()

        with (
            patch("yoto_lib.covers.itunes.extract_album_art", return_value=None),
            patch("yoto_lib.covers.itunes.search_itunes_album", return_value=[api_result]),
            patch("yoto_lib.covers.itunes.match_album", return_value=api_result),
            patch("yoto_lib.covers.itunes.httpx.get", return_value=mock_response),
            patch("yoto_lib.covers.itunes.embed_album_art", return_value=True),
            patch("yoto_lib.covers.itunes.write_tags") as mock_write_tags,
        ):
            # tags already have genre but not date or copyright
            enrich_from_itunes(
                mka,
                {"artist": "Artist", "album": "Album", "genre": "Pop"},
                cache,
            )

        mock_write_tags.assert_called_once()
        written_tags = mock_write_tags.call_args[0][1]
        assert "genre" not in written_tags  # already present, not overwritten
        assert written_tags["date"] == "2020-01-01T00:00:00Z"
        assert written_tags["copyright"] == "2020 Label"


class TestImportIntegration:
    @needs_ffmpeg
    def test_import_calls_enrich(self, tmp_path):
        """Verify import_cmd calls enrich_from_itunes for each wrapped track."""
        from yoto_cli.main import cli

        # Create a minimal WAV
        wav = tmp_path / "source" / "track.wav"
        wav.parent.mkdir()
        sample_rate = 44100
        data_size = 2
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16, 1,
            1, sample_rate, sample_rate * 2, 2, 16,
            b"data", data_size,
        )
        wav.write_bytes(header + b"\x00\x00")

        output = tmp_path / "output"

        with (
            patch("yoto_cli.main.enrich_from_itunes") as mock_enrich,
            patch("yoto_cli.main.generate_description"),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["import", str(wav.parent), "-o", str(output)])

        assert result.exit_code == 0, result.output
        assert mock_enrich.call_count >= 1
        # First arg is the MKA path, second is the tags dict, third is the cache dict
        call_args = mock_enrich.call_args_list[0][0]
        assert call_args[0].suffix == ".mka"
        assert isinstance(call_args[1], dict)
        assert isinstance(call_args[2], dict)

    @needs_ffmpeg
    def test_import_calls_get_lyrics(self, tmp_path):
        """Verify import_cmd calls get_lyrics and writes LYRICS tag when found."""
        from yoto_cli.main import cli

        wav = tmp_path / "source" / "track.wav"
        wav.parent.mkdir()
        sample_rate = 44100
        data_size = 2
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16, 1,
            1, sample_rate, sample_rate * 2, 2, 16,
            b"data", data_size,
        )
        wav.write_bytes(header + b"\x00\x00")

        output = tmp_path / "output"

        with (
            patch("yoto_cli.main.enrich_from_itunes"),
            patch("yoto_cli.main.generate_description"),
            patch("yoto_cli.main.get_lyrics", return_value=("La la la", "lrclib")) as mock_lyrics,
            patch("yoto_cli.main.write_tags") as mock_write_tags,
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["import", str(wav.parent), "-o", str(output)])

        assert result.exit_code == 0, result.output
        mock_lyrics.assert_called_once()
        # write_tags should have been called with lyrics
        lyrics_calls = [
            call for call in mock_write_tags.call_args_list
            if "lyrics" in call[0][1]
        ]
        assert len(lyrics_calls) >= 1
        assert lyrics_calls[0][0][1]["lyrics"] == "La la la"


import httpx as httpx_client

needs_network = pytest.mark.skipif(
    os.environ.get("SKIP_NETWORK_TESTS", "0") == "1",
    reason="Network tests disabled",
)


@needs_network
class TestItunesE2E:
    def test_search_and_match_daniel_tiger(self):
        """Hit the real iTunes API and verify we can match a known album."""
        results = search_itunes_album(
            "Daniel Tiger",
            "Daniel Tiger's Neighborhood: Life's Little Lessons",
        )
        assert len(results) > 0

        matched = match_album(
            results,
            "Daniel Tiger",
            "Daniel Tiger's Neighborhood: Life's Little Lessons",
        )
        assert matched is not None
        assert "Daniel Tiger" in matched["artistName"]
        assert "artworkUrl100" in matched

        art_url = _artwork_url(matched["artworkUrl100"], 600)
        response = httpx_client.get(art_url, follow_redirects=True, timeout=10.0)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert len(response.content) > 1000
