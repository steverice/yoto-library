"""Tests for icon pipeline (icons.py)."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from yoto_lib.icon_llm import CONFIDENCE_HIGH, CONFIDENCE_LOW
from yoto_lib.icons import (
    ICNS_SIZES,
    ICNS_TYPE_MAP,
    ICON_SIZE,
    build_icon_prompt,
    extract_icon_hash,
    generate_icns_sizes,
    generate_retrodiffusion_icons,
    generate_track_icon,
    match_public_icon,
    nearest_neighbor_upscale,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_16x16(color: str = "red") -> Image.Image:
    """Return a solid-color 16x16 RGBA image."""
    return Image.new("RGBA", (ICON_SIZE, ICON_SIZE), color=color)


def _make_checkerboard_16() -> Image.Image:
    """Return a 16x16 black-and-white checkerboard (1-pixel squares)."""
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE))
    pixels = img.load()
    for y in range(ICON_SIZE):
        for x in range(ICON_SIZE):
            pixels[x, y] = (255, 255, 255) if (x + y) % 2 == 0 else (0, 0, 0)
    return img


# ── TestNearestNeighborUpscale ────────────────────────────────────────────────


class TestNearestNeighborUpscale:
    def test_16_to_32_size(self):
        """Upscaling 16x16 to 32 produces a 32x32 image."""
        icon = _make_16x16("blue")
        result = nearest_neighbor_upscale(icon, 32)
        assert result.size == (32, 32)

    def test_checkerboard_preserves_pixel_grid(self):
        """Nearest-neighbor upscale of a checkerboard keeps hard pixel boundaries."""
        icon = _make_checkerboard_16()
        result = nearest_neighbor_upscale(icon, 32)
        assert result.size == (32, 32)

        pixels = result.load()
        # Each original pixel maps to a 2x2 block in the output.
        # Verify the top-left 4 output pixels match the 2x2 expected pattern:
        #   (0,0) orig → white  →  output (0,0),(1,0),(0,1),(1,1) all white
        #   (1,0) orig → black  →  output (2,0),(3,0),(2,1),(3,1) all black
        white = (255, 255, 255)
        black = (0, 0, 0)
        for dy in range(2):
            for dx in range(2):
                assert pixels[dx, dy] == white, f"pixel ({dx},{dy}) should be white"
                assert pixels[2 + dx, dy] == black, f"pixel ({2+dx},{dy}) should be black"


# ── TestGenerateIcnsSizes ─────────────────────────────────────────────────────


class TestGenerateIcnsSizes:
    def test_generates_all_sizes(self):
        """generate_icns_sizes returns an image for every size in ICNS_SIZES."""
        icon = _make_16x16()
        result = generate_icns_sizes(icon)

        assert set(result.keys()) == set(ICNS_SIZES)
        for size in ICNS_SIZES:
            assert result[size].size == (size, size), (
                f"Expected {size}x{size}, got {result[size].size}"
            )

    def test_all_sizes_covered_by_type_map(self):
        """Every size in ICNS_SIZES has a corresponding entry in ICNS_TYPE_MAP."""
        for size in ICNS_SIZES:
            assert size in ICNS_TYPE_MAP, f"Size {size} missing from ICNS_TYPE_MAP"
            assert len(ICNS_TYPE_MAP[size]) == 4, (
                f"Type tag for size {size} must be exactly 4 bytes"
            )


# ── TestMatchPublicIcon ───────────────────────────────────────────────────────


class TestMatchPublicIcon:
    def _icons(self, entries: list[tuple[str, str]]) -> list[dict]:
        """Build a list of public-icon dicts from (title, mediaId) pairs."""
        return [{"title": title, "mediaId": mid} for title, mid in entries]

    def test_exact_title_match(self):
        """An icon whose title exactly equals the track title returns its mediaId."""
        icons = self._icons([
            ("Jungle Adventure", "id-jungle"),
            ("Space Explorer", "id-space"),
        ])
        result = match_public_icon("Jungle Adventure", icons)
        assert result == "id-jungle"

    def test_partial_match(self):
        """An icon with significant word overlap is returned (score >= 0.5)."""
        icons = self._icons([
            ("Adventure Time", "id-adventure"),
            ("Bedtime Story", "id-bedtime"),
        ])
        result = match_public_icon("Adventure Time Stories", icons)
        assert result == "id-adventure"

    def test_no_match_returns_none(self):
        """Returns None when no icon has a score >= 0.5."""
        icons = self._icons([
            ("Jungle Adventure", "id-jungle"),
            ("Space Explorer", "id-space"),
        ])
        result = match_public_icon("Completely Unrelated Topic Here", icons)
        assert result is None

    def test_falls_back_to_name_field(self):
        """Falls back to 'name' if 'title' is missing."""
        icons = [{"name": "Jungle Adventure", "mediaId": "id-jungle"}]
        result = match_public_icon("Jungle Adventure", icons)
        assert result == "id-jungle"


# ── TestExtractIconHash ──────────────────────────────────────────────────────


class TestExtractIconHash:
    def test_yoto_format(self):
        assert extract_icon_hash("yoto:#abc123") == "abc123"

    def test_url_format(self):
        assert extract_icon_hash("https://media.api.yotoplay.com/icons/def456") == "def456"

    def test_empty_string(self):
        assert extract_icon_hash("") is None

    def test_bare_hash_passes_through(self):
        """A bare string is returned as-is (treated as a hash/mediaId)."""
        assert extract_icon_hash("abc123") == "abc123"


# ── TestBuildIconPrompt ──────────────────────────────────────────────────────


class TestBuildIconPrompt:
    def test_includes_title(self):
        prompt = build_icon_prompt("Jungle Adventure")
        assert "Jungle Adventure" in prompt

    def test_grid_instructions(self):
        prompt = build_icon_prompt("Song")
        assert "8x8" in prompt
        assert "1024x1024" in prompt
        assert "128x128" in prompt

    def test_style_constraints(self):
        prompt = build_icon_prompt("Song")
        assert "bold" in prompt.lower()
        assert "flat" in prompt.lower()
        assert "no" in prompt.lower() and "text" in prompt.lower()


# ── TestGenerateTrackIcon ────────────────────────────────────────────────────


def _make_1024x1024() -> bytes:
    """Create a 1024x1024 test image with a distinct center tile."""
    img = Image.new("RGB", (1024, 1024), color="blue")
    # Paint the center tile (4*128=512, 4*128=512) -> (640, 640) red
    for y in range(512, 640):
        for x in range(512, 640):
            img.putpixel((x, y), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestGenerateTrackIcon:
    def test_returns_16x16_png(self):
        """Grid image → crop center tile → downscale to 16x16 PNG."""
        fake_image = _make_1024x1024()
        mock_provider = MagicMock()
        mock_provider.generate.return_value = fake_image

        with (
            patch("yoto_lib.icons.generate_retrodiffusion_icon", return_value=(None, None)),
            patch("yoto_lib.providers.get_provider", return_value=mock_provider),
        ):
            result = generate_track_icon("Test Song")

        assert result is not None
        img = Image.open(io.BytesIO(result))
        assert img.size == (16, 16)

    def test_returns_none_on_no_provider(self):
        """Returns None if no image provider is configured."""
        with (
            patch("yoto_lib.icons.generate_retrodiffusion_icon", return_value=(None, None)),
            patch("yoto_lib.providers.get_provider", side_effect=ValueError("no key")),
        ):
            result = generate_track_icon("Test Song")
        assert result is None

    def test_returns_none_on_generate_error(self):
        """Returns None if the provider fails to generate."""
        mock_provider = MagicMock()
        mock_provider.generate.side_effect = OSError("API error")

        with (
            patch("yoto_lib.icons.generate_retrodiffusion_icon", return_value=(None, None)),
            patch("yoto_lib.providers.get_provider", return_value=mock_provider),
        ):
            result = generate_track_icon("Test Song")
        assert result is None


class TestGenerateRetrodiffusionIcons:
    def test_returns_three_raw_and_processed_pairs(self):
        """Returns list of (raw_bytes, processed_Image) tuples."""
        fake_png = Image.new("RGB", (16, 16), "black")
        fake_png.putpixel((8, 8), (255, 0, 0))
        buf = io.BytesIO()
        fake_png.save(buf, format="PNG")
        fake_png_bytes = buf.getvalue()

        mock_provider = MagicMock()
        mock_provider.generate.return_value = fake_png_bytes

        with patch(
            "yoto_lib.icons.RetroDiffusionProvider",
            return_value=mock_provider,
        ):
            results = generate_retrodiffusion_icons(["desc1", "desc2", "desc3"])

        assert len(results) == 3
        for raw_bytes, processed_img in results:
            assert isinstance(raw_bytes, bytes)
            assert processed_img.size == (16, 16)

    def test_returns_empty_on_provider_init_failure(self):
        """Returns empty list when provider init fails."""
        with patch(
            "yoto_lib.icons.RetroDiffusionProvider",
            side_effect=ValueError("no API key"),
        ):
            results = generate_retrodiffusion_icons(["desc"])

        assert results == []


class TestResolveIconsZones:
    """Tests for the three-zone confidence logic in resolve_icons."""

    def _make_playlist(self, tmp_path, track_names):
        """Create a minimal Playlist with MKA stubs."""
        from yoto_lib.playlist import Playlist

        playlist_dir = tmp_path / "test_playlist"
        playlist_dir.mkdir()
        for name in track_names:
            (playlist_dir / name).write_bytes(b"")

        playlist = MagicMock(spec=Playlist)
        playlist.path = playlist_dir
        playlist.track_files = track_names
        return playlist

    def _make_icon_png(self):
        buf = io.BytesIO()
        Image.new("RGB", (16, 16), "blue").save(buf, format="PNG")
        return buf.getvalue()

    def test_high_confidence_uses_yoto_icon(self, tmp_path):
        """Score >= 0.8: uses Yoto icon directly, no AI generation."""
        playlist = self._make_playlist(tmp_path, ["track.mka"])
        api = MagicMock()

        icon_png = self._make_icon_png()
        catalog = [{"mediaId": "yoto-dino", "title": "Dinosaur"}]

        with (
            patch("yoto_lib.icons.mka.get_attachment", side_effect=OSError),
            patch("yoto_lib.icons.mka.read_tags", return_value={"title": "Dinosaur Story"}),
            patch("yoto_lib.icons.get_catalog", return_value=catalog),
            patch("yoto_lib.icons.match_icon_llm", return_value=("yoto-dino", 0.92)),
            patch("yoto_lib.icons.download_icon", return_value=icon_png),
            patch("yoto_lib.icons.apply_icon_to_mka"),
            patch("yoto_lib.icons.set_macos_file_icon"),
            patch("yoto_lib.icons.generate_retrodiffusion_icons") as mock_gen,
        ):
            from yoto_lib.icons import resolve_icons
            result = resolve_icons(playlist, api)

        assert result["track.mka"] == "yoto-dino"
        mock_gen.assert_not_called()

    def test_low_confidence_generates_ai_picks_best(self, tmp_path):
        """Score < 0.4: generates 3 AI icons, LLM picks best of 3."""
        playlist = self._make_playlist(tmp_path, ["track.mka"])
        api = MagicMock()

        icon_png = self._make_icon_png()
        catalog = [{"mediaId": "star-id", "title": "Star"}]

        with (
            patch("yoto_lib.icons.mka.get_attachment", side_effect=OSError),
            patch("yoto_lib.icons.mka.read_tags", return_value={"title": "Quantum Physics"}),
            patch("yoto_lib.icons.get_catalog", return_value=catalog),
            patch("yoto_lib.icons.match_icon_llm", return_value=(None, 0.1)),
            patch("yoto_lib.icons.generate_retrodiffusion_icons") as mock_gen,
            patch("yoto_lib.icons.compare_icons_llm", return_value=(2, [0.5, 0.9, 0.6])),
            patch("yoto_lib.icons.apply_icon_to_mka"),
            patch("yoto_lib.icons._upload_icon_bytes", return_value="uploaded-id"),
            patch("yoto_lib.icons.set_macos_file_icon"),
        ):
            img = Image.new("RGBA", (16, 16), "red")
            mock_gen.return_value = [(icon_png, img)] * 3

            from yoto_lib.icons import resolve_icons
            result = resolve_icons(playlist, api)

        assert result["track.mka"] == "uploaded-id"
        mock_gen.assert_called_once()

    def test_gray_zone_compares_four_candidates(self, tmp_path):
        """Score 0.4-0.8: generates 3 AI + includes Yoto icon, LLM picks best of 4."""
        playlist = self._make_playlist(tmp_path, ["track.mka"])
        api = MagicMock()

        icon_png = self._make_icon_png()
        catalog = [{"mediaId": "yoto-dino", "title": "Dinosaur"}]

        with (
            patch("yoto_lib.icons.mka.get_attachment", side_effect=OSError),
            patch("yoto_lib.icons.mka.read_tags", return_value={"title": "Dino Fun"}),
            patch("yoto_lib.icons.get_catalog", return_value=catalog),
            patch("yoto_lib.icons.match_icon_llm", return_value=("yoto-dino", 0.6)),
            patch("yoto_lib.icons.download_icon", return_value=icon_png),
            patch("yoto_lib.icons.generate_retrodiffusion_icons") as mock_gen,
            patch("yoto_lib.icons.compare_icons_llm") as mock_compare,
            patch("yoto_lib.icons.apply_icon_to_mka"),
            patch("yoto_lib.icons._upload_icon_bytes", return_value="uploaded-id"),
            patch("yoto_lib.icons.set_macos_file_icon"),
        ):
            img = Image.new("RGBA", (16, 16), "red")
            mock_gen.return_value = [(icon_png, img)] * 3
            mock_compare.return_value = (4, [0.5, 0.6, 0.5, 0.85])

            from yoto_lib.icons import resolve_icons
            result = resolve_icons(playlist, api)

        assert result["track.mka"] == "yoto-dino"
        call_args = mock_compare.call_args
        ai_candidates = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("candidates", [])
        assert len(ai_candidates) == 3
        assert call_args.kwargs.get("yoto_icon") is not None or (len(call_args.args) > 2 and call_args.args[2] is not None)


class TestResolveIconsLexicalShortcut:
    def _make_playlist(self, tmp_path, track_names):
        from yoto_lib.playlist import Playlist
        playlist_dir = tmp_path / "test_playlist"
        playlist_dir.mkdir()
        for name in track_names:
            (playlist_dir / name).write_bytes(b"")
        playlist = MagicMock(spec=Playlist)
        playlist.path = playlist_dir
        playlist.track_files = track_names
        return playlist

    def _make_icon_png(self):
        buf = io.BytesIO()
        Image.new("RGB", (16, 16), "blue").save(buf, format="PNG")
        return buf.getvalue()

    def test_exact_title_match_skips_llm(self, tmp_path):
        """When track title exactly matches an icon title, skip the LLM call."""
        playlist = self._make_playlist(tmp_path, ["Dinosaur.mka"])
        api = MagicMock()
        icon_png = self._make_icon_png()
        catalog = [{"mediaId": "dino-id", "title": "Dinosaur"}]

        with (
            patch("yoto_lib.icons.mka.get_attachment", side_effect=OSError),
            patch("yoto_lib.icons.mka.read_tags", return_value={"title": "Dinosaur"}),
            patch("yoto_lib.icons.get_catalog", return_value=catalog),
            patch("yoto_lib.icons.match_icon_llm") as mock_llm,
            patch("yoto_lib.icons.download_icon", return_value=icon_png),
            patch("yoto_lib.icons.apply_icon_to_mka"),
            patch("yoto_lib.icons.set_macos_file_icon"),
        ):
            from yoto_lib.icons import resolve_icons
            result = resolve_icons(playlist, api)

        assert result["Dinosaur.mka"] == "dino-id"
        mock_llm.assert_not_called()

    def test_partial_lexical_match_does_not_skip_llm(self, tmp_path):
        """A partial match (e.g. 'Dinosaur Stories' vs 'Dinosaur') still goes to LLM."""
        playlist = self._make_playlist(tmp_path, ["track.mka"])
        api = MagicMock()
        icon_png = self._make_icon_png()
        catalog = [{"mediaId": "dino-id", "title": "Dinosaur"}]

        with (
            patch("yoto_lib.icons.mka.get_attachment", side_effect=OSError),
            patch("yoto_lib.icons.mka.read_tags", return_value={"title": "Dinosaur Stories"}),
            patch("yoto_lib.icons.get_catalog", return_value=catalog),
            patch("yoto_lib.icons.match_icon_llm", return_value=("dino-id", 0.85)) as mock_llm,
            patch("yoto_lib.icons.download_icon", return_value=icon_png),
            patch("yoto_lib.icons.apply_icon_to_mka"),
            patch("yoto_lib.icons.set_macos_file_icon"),
        ):
            from yoto_lib.icons import resolve_icons
            result = resolve_icons(playlist, api)

        mock_llm.assert_called_once()
