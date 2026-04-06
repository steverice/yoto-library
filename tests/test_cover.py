"""Tests for cover art pipeline."""

from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from yoto_lib.cover import (
    COVER_HEIGHT,
    COVER_WIDTH,
    build_cover_prompt,
    compare_covers,
    generate_cover_if_missing,
    pad_to_cover,
    reframe_album_art,
    resize_cover,
    try_shared_album_art,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_png(path: Path, width: int, height: int, color: str = "blue") -> Path:
    """Create a solid-color PNG at the given path."""
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, format="PNG")
    return path


# ── TestResizeCover ───────────────────────────────────────────────────────────


class TestResizeCover:
    def test_resize_to_cover_dimensions(self, tmp_path):
        """Output image must be exactly COVER_WIDTH x COVER_HEIGHT."""
        source = _make_png(tmp_path / "source.png", 800, 600)
        output = tmp_path / "output.png"

        resize_cover(source, output)

        result = Image.open(output)
        assert result.size == (COVER_WIDTH, COVER_HEIGHT)

    def test_resize_preserves_aspect_by_cropping(self, tmp_path):
        """A wide image is cropped on the sides; a tall image is cropped top/bottom."""
        # Wide image: wider than 638:1011 ratio → sides should be cropped
        wide_source = _make_png(tmp_path / "wide.png", 2000, 1011)
        wide_output = tmp_path / "wide_out.png"
        resize_cover(wide_source, wide_output)
        assert Image.open(wide_output).size == (COVER_WIDTH, COVER_HEIGHT)

        # Tall image: taller than 638:1011 ratio → top/bottom should be cropped
        tall_source = _make_png(tmp_path / "tall.png", 638, 2000)
        tall_output = tmp_path / "tall_out.png"
        resize_cover(tall_source, tall_output)
        assert Image.open(tall_output).size == (COVER_WIDTH, COVER_HEIGHT)


# ── TestBuildCoverPrompt ──────────────────────────────────────────────────────


class TestBuildCoverPrompt:
    def test_includes_description(self):
        """Prompt must contain the provided description."""
        prompt = build_cover_prompt(
            description="A magical forest adventure",
            track_titles=[],
            artists=[],
        )
        assert "magical forest adventure" in prompt

    def test_includes_track_titles(self):
        """Prompt must contain provided track titles (up to 10)."""
        titles = [f"Track {i}" for i in range(12)]
        prompt = build_cover_prompt(
            description=None,
            track_titles=titles,
            artists=[],
        )
        # First 10 titles should appear
        for i in range(10):
            assert f"Track {i}" in prompt
        # 11th and 12th should NOT appear
        assert "Track 10" not in prompt
        assert "Track 11" not in prompt

    def test_works_with_empty_inputs(self):
        """Prompt should still be a valid non-empty string with no inputs."""
        prompt = build_cover_prompt(
            description=None,
            track_titles=[],
            artists=[],
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # Must still request portrait illustration and no text
        assert "portrait" in prompt.lower() or "illustration" in prompt.lower()
        assert "text" in prompt.lower() or "lettering" in prompt.lower()

    def test_includes_playlist_title(self):
        """Prompt must contain the playlist title when provided."""
        prompt = build_cover_prompt(
            description=None,
            track_titles=[],
            artists=[],
            playlist_title="Daniel Tiger's Neighborhood",
        )
        assert "Daniel Tiger's Neighborhood" in prompt
        assert "playlist name" in prompt.lower()


# ── TestGenerateCoverIfMissing ────────────────────────────────────────────────


class TestGenerateCoverIfMissing:
    def test_skips_when_cover_exists(self, tmp_path):
        """generate_cover_if_missing does nothing when playlist.has_cover is True."""
        playlist = MagicMock()
        playlist.has_cover = True

        with patch("yoto_lib.cover.get_provider") as mock_get_provider:
            generate_cover_if_missing(playlist)

        mock_get_provider.assert_not_called()

    def test_generates_when_missing(self, tmp_path):
        """Generates and saves cover.png with correct dimensions when missing."""
        import io

        # Create a real PNG in memory to use as fake provider output
        fake_img = Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT), color="red")
        buf = io.BytesIO()
        fake_img.save(buf, format="PNG")
        fake_png_bytes = buf.getvalue()

        # Set up a fake playlist pointing at tmp_path
        cover_path = tmp_path / "cover.png"
        playlist = MagicMock()
        playlist.has_cover = False
        playlist.path = tmp_path
        playlist.cover_path = cover_path
        playlist.description = "A fun story"
        playlist.track_files = ["track01.mka", "track02.mka"]

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.generate.return_value = fake_png_bytes

        with patch("yoto_lib.cover.get_provider", return_value=mock_provider), \
             patch("yoto_lib.cover.build_cover_prompt", return_value="test prompt") as mock_prompt, \
             patch("yoto_lib.cover.mka.read_tags") as mock_read_tags:

            mock_read_tags.side_effect = [
                {"title": "Chapter One", "artist": "Jane Doe"},
                {"title": "Chapter Two", "artist": "Jane Doe"},
            ]

            generate_cover_if_missing(playlist)

        # cover.png must exist
        assert cover_path.exists(), "cover.png was not created"

        # cover.png must have correct dimensions
        result = Image.open(cover_path)
        assert result.size == (COVER_WIDTH, COVER_HEIGHT), (
            f"Expected {COVER_WIDTH}x{COVER_HEIGHT}, got {result.size}"
        )

        # prompt builder was called
        mock_prompt.assert_called_once()

        # provider.generate was called with the prompt at 3:4 aspect ratio
        mock_provider.generate.assert_called_once_with(
            "test prompt", 768, 1024
        )


# ── TestTrySharedAlbumArt ────────────────────────────────────────────────────


def _make_png_bytes(width: int, height: int, color: str = "green") -> bytes:
    """Create a PNG image in memory and return its bytes."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestTrySharedAlbumArt:
    def test_returns_false_for_empty_playlist(self):
        """No tracks means no shared art."""
        playlist = MagicMock()
        playlist.track_files = []
        assert not try_shared_album_art(playlist)

    def test_returns_false_when_no_art(self, tmp_path):
        """Returns False when tracks have no embedded art."""
        playlist = MagicMock()
        playlist.track_files = ["track01.mka"]
        playlist.path = tmp_path
        (tmp_path / "track01.mka").touch()

        with patch("yoto_lib.cover.mka.extract_album_art", return_value=None):
            assert not try_shared_album_art(playlist)

    def test_returns_false_when_art_differs(self, tmp_path):
        """Returns False when tracks have different album art."""
        playlist = MagicMock()
        playlist.track_files = ["track01.mka", "track02.mka"]
        playlist.path = tmp_path
        (tmp_path / "track01.mka").touch()
        (tmp_path / "track02.mka").touch()

        art_a = _make_png_bytes(500, 500, "red")
        art_b = _make_png_bytes(500, 500, "blue")

        with patch("yoto_lib.cover.mka.extract_album_art", side_effect=[art_a, art_b]):
            assert not try_shared_album_art(playlist)

    def test_returns_false_when_some_tracks_missing_art(self, tmp_path):
        """Returns False when only some tracks have art."""
        playlist = MagicMock()
        playlist.track_files = ["track01.mka", "track02.mka"]
        playlist.path = tmp_path
        (tmp_path / "track01.mka").touch()
        (tmp_path / "track02.mka").touch()

        art = _make_png_bytes(500, 500)

        with patch("yoto_lib.cover.mka.extract_album_art", side_effect=[art, None]):
            assert not try_shared_album_art(playlist)

    def test_returns_true_and_saves_cover_when_all_match(self, tmp_path):
        """Saves reframed cover when all tracks share the same art."""
        cover_path = tmp_path / "cover.png"
        playlist = MagicMock()
        playlist.track_files = ["track01.mka", "track02.mka", "track03.mka"]
        playlist.path = tmp_path
        playlist.cover_path = cover_path
        (tmp_path / "track01.mka").touch()
        (tmp_path / "track02.mka").touch()
        (tmp_path / "track03.mka").touch()

        shared_art = _make_png_bytes(500, 500)

        with (
            patch("yoto_lib.cover.mka.extract_album_art", return_value=shared_art),
            patch("yoto_lib.cover.reframe_album_art") as mock_reframe,
        ):
            result = try_shared_album_art(playlist)

        assert result is True
        mock_reframe.assert_called_once_with(shared_art, cover_path, log=None)

    def test_generate_cover_tries_shared_art_first(self):
        """generate_cover_if_missing tries shared art before AI generation."""
        playlist = MagicMock()
        playlist.has_cover = False

        with patch("yoto_lib.cover.try_shared_album_art", return_value=True) as mock_shared, \
             patch("yoto_lib.cover.get_provider") as mock_provider:
            generate_cover_if_missing(playlist)

        mock_shared.assert_called_once_with(playlist, log=None)
        mock_provider.assert_not_called()


# ── TestPadToCover ────────────────────────────────────────────────────────────


class TestPadToCover:
    def test_pads_square_image_to_portrait(self):
        """A 500x500 square image should be padded top/bottom to 638x1011."""
        art_bytes = _make_png_bytes(500, 500, "green")
        result = pad_to_cover(art_bytes)
        img = Image.open(io.BytesIO(result))
        assert img.size == (COVER_WIDTH, COVER_HEIGHT)

    def test_preserves_original_art_in_center(self):
        """The original art should be centered vertically in the output."""
        art_bytes = _make_png_bytes(500, 500, "red")
        result = pad_to_cover(art_bytes)
        img = Image.open(io.BytesIO(result))
        # The center pixel should be red (from the original art)
        center_pixel = img.getpixel((COVER_WIDTH // 2, COVER_HEIGHT // 2))
        assert center_pixel[0] > 200  # red channel high
        assert center_pixel[1] < 50   # green channel low

    def test_padding_uses_edge_color(self):
        """The padding area should match the edge color of the source image."""
        art_bytes = _make_png_bytes(500, 500, "#2baf45")
        result = pad_to_cover(art_bytes)
        img = Image.open(io.BytesIO(result))
        # Top-left corner is in the padding area
        top_pixel = img.getpixel((10, 5))
        assert top_pixel[1] > 150  # green channel dominant

    def test_already_portrait_image(self):
        """An image already taller than wide should still produce correct dimensions."""
        art_bytes = _make_png_bytes(400, 700, "blue")
        result = pad_to_cover(art_bytes)
        img = Image.open(io.BytesIO(result))
        assert img.size == (COVER_WIDTH, COVER_HEIGHT)


# ── TestCompareCovers ─────────────────────────────────────────────────────────


class TestCompareCovers:
    def test_returns_winner_a(self):
        """Returns 'a' when Claude picks the padded version."""
        padded = _make_png_bytes(638, 1011, "green")
        outpainted = _make_png_bytes(638, 1011, "blue")

        with patch("yoto_lib.cover._call_claude", return_value='"A"'):
            winner = compare_covers(padded, outpainted)
        assert winner == "a"

    def test_returns_winner_b(self):
        """Returns 'b' when Claude picks the outpainted version."""
        padded = _make_png_bytes(638, 1011, "green")
        outpainted = _make_png_bytes(638, 1011, "blue")

        with patch("yoto_lib.cover._call_claude", return_value='"B"'):
            winner = compare_covers(padded, outpainted)
        assert winner == "b"

    def test_returns_b_on_failure(self):
        """Falls back to 'b' (outpainted) when Claude call fails."""
        padded = _make_png_bytes(638, 1011, "green")
        outpainted = _make_png_bytes(638, 1011, "blue")

        with patch("yoto_lib.cover._call_claude", return_value=None):
            winner = compare_covers(padded, outpainted)
        assert winner == "b"


# ── TestReframeAlbumArt ───────────────────────────────────────────────────────


class TestReframeAlbumArt:
    def test_saves_cover_with_correct_dimensions(self, tmp_path):
        """Output file must be COVER_WIDTH x COVER_HEIGHT."""
        art_bytes = _make_png_bytes(500, 500, "green")
        output = tmp_path / "cover.png"

        with (
            patch("yoto_lib.cover.get_provider") as mock_get_provider,
            patch("yoto_lib.cover.compare_covers", return_value="a"),
        ):
            mock_provider = MagicMock()
            mock_provider.edit.return_value = _make_png_bytes(638, 1011, "blue")
            mock_get_provider.return_value = mock_provider
            reframe_album_art(art_bytes, output)

        assert output.exists()
        img = Image.open(output)
        assert img.size == (COVER_WIDTH, COVER_HEIGHT)

    def test_uses_outpainted_when_claude_picks_b(self, tmp_path):
        """When Claude picks B, the outpainted version is saved."""
        art_bytes = _make_png_bytes(500, 500, "green")
        output = tmp_path / "cover.png"

        outpainted_bytes = _make_png_bytes(638, 1011, "blue")

        with (
            patch("yoto_lib.cover.get_provider") as mock_get_provider,
            patch("yoto_lib.cover.compare_covers", return_value="b"),
        ):
            mock_provider = MagicMock()
            mock_provider.edit.return_value = outpainted_bytes
            mock_get_provider.return_value = mock_provider
            reframe_album_art(art_bytes, output)

        # The saved cover should be blue (outpainted), not green (padded)
        img = Image.open(output)
        center = img.getpixel((COVER_WIDTH // 2, COVER_HEIGHT // 2))
        assert center[2] > 200  # blue channel dominant

    def test_falls_back_to_padded_when_outpaint_fails(self, tmp_path):
        """When the provider's edit() fails, use the padded version."""
        art_bytes = _make_png_bytes(500, 500, "green")
        output = tmp_path / "cover.png"

        with (
            patch("yoto_lib.cover.get_provider") as mock_get_provider,
            patch("yoto_lib.cover.compare_covers") as mock_compare,
        ):
            mock_provider = MagicMock()
            mock_provider.edit.side_effect = OSError("API error")
            mock_get_provider.return_value = mock_provider
            reframe_album_art(art_bytes, output)

        # Should not have called compare (only one candidate)
        mock_compare.assert_not_called()
        assert output.exists()
        img = Image.open(output)
        assert img.size == (COVER_WIDTH, COVER_HEIGHT)

    def test_falls_back_to_padded_when_no_provider(self, tmp_path):
        """When get_provider raises, use the padded version."""
        art_bytes = _make_png_bytes(500, 500, "green")
        output = tmp_path / "cover.png"

        with (
            patch("yoto_lib.cover.get_provider", side_effect=ValueError("no provider")),
            patch("yoto_lib.cover.compare_covers") as mock_compare,
        ):
            reframe_album_art(art_bytes, output)

        mock_compare.assert_not_called()
        assert output.exists()


class TestTrySharedAlbumArtReframe:
    def test_calls_reframe_instead_of_resize(self, tmp_path):
        """try_shared_album_art should call reframe_album_art, not resize_cover."""
        cover_path = tmp_path / "cover.png"
        playlist = MagicMock()
        playlist.track_files = ["track01.mka", "track02.mka"]
        playlist.path = tmp_path
        playlist.cover_path = cover_path
        (tmp_path / "track01.mka").touch()
        (tmp_path / "track02.mka").touch()

        shared_art = _make_png_bytes(500, 500)

        with (
            patch("yoto_lib.cover.mka.extract_album_art", return_value=shared_art),
            patch("yoto_lib.cover.reframe_album_art") as mock_reframe,
            patch("yoto_lib.cover.resize_cover") as mock_resize,
        ):
            result = try_shared_album_art(playlist)

        assert result is True
        mock_reframe.assert_called_once_with(shared_art, cover_path, log=None)
        mock_resize.assert_not_called()


# ── TestReframeE2E ────────────────────────────────────────────────────────────


needs_network = pytest.mark.skipif(
    os.environ.get("SKIP_NETWORK_TESTS", "0") == "1",
    reason="Network tests disabled",
)


@needs_network
class TestReframeE2E:
    def test_reframe_with_real_outpainting(self, tmp_path):
        """Integration test: pad + outpaint + compare with real providers."""
        # Create a solid-green test image simulating album art
        art_bytes = _make_png_bytes(600, 600, "#2baf45")
        output = tmp_path / "cover.png"

        # This test exercises the full pipeline with real API calls
        # It may fail if no OPENAI_API_KEY / GEMINI_API_KEY is configured
        try:
            reframe_album_art(art_bytes, output)
        except (ValueError, Exception) as exc:
            pytest.skip(f"Provider not configured: {exc}")

        assert output.exists()
        img = Image.open(output)
        assert img.size == (COVER_WIDTH, COVER_HEIGHT)
