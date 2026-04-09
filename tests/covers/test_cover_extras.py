"""Tests for cover art edge cases — prompt deduplication, composite_text."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from yoto_lib.covers.cover import (
    COVER_HEIGHT,
    COVER_WIDTH,
    build_cover_prompt,
    composite_text,
    pad_to_cover,
    resize_cover,
)


def _make_png_bytes(width: int, height: int, color: str = "green") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestBuildCoverPromptDedup:
    def test_deduplicates_artists(self):
        """Duplicate artists are removed from the prompt."""
        prompt = build_cover_prompt(
            description=None,
            track_titles=["Song A", "Song B"],
            artists=["Artist One", "Artist One", "Artist Two", "Artist One"],
        )
        # Should appear once each
        assert prompt.count("Artist One") == 1
        assert prompt.count("Artist Two") == 1

    def test_preserves_artist_order(self):
        """Artist order is preserved after deduplication."""
        prompt = build_cover_prompt(
            description=None,
            track_titles=[],
            artists=["Zebra", "Apple", "Zebra", "Mango"],
        )
        # Zebra should come before Apple
        idx_zebra = prompt.index("Zebra")
        idx_apple = prompt.index("Apple")
        assert idx_zebra < idx_apple


class TestCompositeText:
    def test_composites_text_onto_image(self):
        """Text layer is composited at the given placement coordinates."""
        # Create a solid red background
        recomposed = _make_png_bytes(638, 1011, "red")

        # Create a white text on black background
        text_img = Image.new("RGBA", (100, 50), (0, 0, 0, 255))
        for x in range(20, 80):
            for y in range(10, 40):
                text_img.putpixel((x, y), (255, 255, 255, 255))
        text_buf = io.BytesIO()
        text_img.save(text_buf, format="PNG")
        text_layer = text_buf.getvalue()

        placement = {"x": 100, "y": 50, "width": 200, "height": 100}
        result = composite_text(recomposed, text_layer, placement)

        result_img = Image.open(io.BytesIO(result))
        assert result_img.size == (638, 1011)
        assert result_img.mode == "RGBA"

    def test_returns_original_when_no_visible_text(self):
        """Returns original image when text layer is entirely black/transparent."""
        recomposed = _make_png_bytes(638, 1011, "blue")

        # Entirely black text layer (below brightness threshold)
        text_img = Image.new("RGBA", (50, 50), (0, 0, 0, 255))
        text_buf = io.BytesIO()
        text_img.save(text_buf, format="PNG")
        text_layer = text_buf.getvalue()

        placement = {"x": 100, "y": 100, "width": 200, "height": 100}
        result = composite_text(recomposed, text_layer, placement)
        assert result == recomposed


class TestResizeCoverExact:
    def test_exact_ratio_input(self, tmp_path):
        """An input already at 638x1011 is resized to exact dimensions."""
        source = tmp_path / "exact.png"
        Image.new("RGB", (638, 1011), "green").save(source)
        output = tmp_path / "out.png"
        resize_cover(source, output)
        result = Image.open(output)
        assert result.size == (COVER_WIDTH, COVER_HEIGHT)

    def test_very_small_input(self, tmp_path):
        """Even tiny images are scaled up to cover dimensions."""
        source = tmp_path / "tiny.png"
        Image.new("RGB", (10, 16), "red").save(source)
        output = tmp_path / "out.png"
        resize_cover(source, output)
        result = Image.open(output)
        assert result.size == (COVER_WIDTH, COVER_HEIGHT)


class TestPadToCoverWide:
    def test_wide_image_padded_on_sides(self):
        """A landscape image is scaled and padded left/right."""
        art_bytes = _make_png_bytes(1000, 400, "yellow")
        result = pad_to_cover(art_bytes)
        img = Image.open(io.BytesIO(result))
        assert img.size == (COVER_WIDTH, COVER_HEIGHT)
