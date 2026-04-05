"""Tests for cover art pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from yoto_lib.cover import (
    COVER_HEIGHT,
    COVER_WIDTH,
    build_cover_prompt,
    generate_cover_if_missing,
    resize_cover,
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

        # provider.generate was called with the prompt
        mock_provider.generate.assert_called_once_with(
            "test prompt", COVER_WIDTH, COVER_HEIGHT
        )
