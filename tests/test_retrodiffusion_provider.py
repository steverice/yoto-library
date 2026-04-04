"""Tests for Retro Diffusion batch generation."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import base64

import pytest


def _fake_response(num_images: int):
    """Build a fake Retro Diffusion API response with num_images base64 PNGs."""
    import io
    from PIL import Image

    images = []
    for _ in range(num_images):
        buf = io.BytesIO()
        Image.new("RGB", (16, 16), "red").save(buf, format="PNG")
        images.append(base64.b64encode(buf.getvalue()).decode())

    mock = MagicMock()
    mock.json.return_value = {"base64_images": images}
    mock.raise_for_status = MagicMock()
    return mock


class TestGenerateBatch:
    def test_returns_requested_count(self):
        """generate_batch returns a list of PNG bytes with the requested count."""
        from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider

        with patch.dict("os.environ", {"RETRODIFFUSION_API_KEY": "test-key"}):
            provider = RetroDiffusionProvider()

        with patch("httpx.post", return_value=_fake_response(3)):
            result = provider.generate_batch("test prompt", 16, 16, count=3)

        assert len(result) == 3
        for item in result:
            assert isinstance(item, bytes)
            assert len(item) > 0

    def test_count_defaults_to_one(self):
        """generate_batch with default count returns a single-element list."""
        from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider

        with patch.dict("os.environ", {"RETRODIFFUSION_API_KEY": "test-key"}):
            provider = RetroDiffusionProvider()

        with patch("httpx.post", return_value=_fake_response(1)):
            result = provider.generate_batch("test prompt", 16, 16)

        assert len(result) == 1

    def test_sends_num_images_in_payload(self):
        """The API request includes the correct num_images field."""
        from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider

        with patch.dict("os.environ", {"RETRODIFFUSION_API_KEY": "test-key"}):
            provider = RetroDiffusionProvider()

        with patch("httpx.post", return_value=_fake_response(3)) as mock_post:
            provider.generate_batch("test prompt", 16, 16, count=3)

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["num_images"] == 3
