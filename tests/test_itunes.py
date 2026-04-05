import subprocess
import struct
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from yoto_lib.itunes import search_itunes_album, match_album, _artwork_url, embed_album_art
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
