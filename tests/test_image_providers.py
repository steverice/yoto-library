"""Tests for image provider interface and implementations."""
import base64
import os
from unittest.mock import MagicMock, patch

import pytest

from yoto_lib.image_providers import ImageProvider, get_provider


class TestImageProviderInterface:
    def test_provider_has_generate_method(self):
        """ImageProvider protocol requires a generate method."""
        assert hasattr(ImageProvider, "generate")

    def test_get_provider_openai(self, monkeypatch):
        """get_provider returns OpenAIProvider when env var is 'openai'."""
        monkeypatch.setenv("YOTO_IMAGE_PROVIDER", "openai")
        with patch("yoto_lib.image_providers.openai_provider.OpenAI"):
            provider = get_provider()
        from yoto_lib.image_providers.openai_provider import OpenAIProvider
        assert isinstance(provider, OpenAIProvider)

    def test_get_provider_gemini(self, monkeypatch):
        """get_provider returns GeminiProvider when env var is 'gemini'."""
        monkeypatch.setenv("YOTO_IMAGE_PROVIDER", "gemini")
        with patch("yoto_lib.image_providers.gemini_provider.genai.Client"):
            provider = get_provider()
        from yoto_lib.image_providers.gemini_provider import GeminiProvider
        assert isinstance(provider, GeminiProvider)

    def test_get_provider_defaults_to_openai(self, monkeypatch):
        """get_provider defaults to OpenAIProvider when env var is not set."""
        monkeypatch.delenv("YOTO_IMAGE_PROVIDER", raising=False)
        with patch("yoto_lib.image_providers.openai_provider.OpenAI"):
            provider = get_provider()
        from yoto_lib.image_providers.openai_provider import OpenAIProvider
        assert isinstance(provider, OpenAIProvider)

    def test_get_provider_raises_for_unknown(self, monkeypatch):
        """get_provider raises ValueError for unknown provider names."""
        monkeypatch.setenv("YOTO_IMAGE_PROVIDER", "unknown_provider")
        with pytest.raises(ValueError, match="unknown_provider"):
            get_provider()


class TestOpenAIProvider:
    def test_generate_returns_bytes(self, monkeypatch):
        """generate() returns PNG bytes decoded from b64_json response."""
        monkeypatch.setenv("YOTO_IMAGE_PROVIDER", "openai")

        # Build a fake PNG bytes payload (just any bytes for the test)
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        fake_b64 = base64.b64encode(fake_png).decode()

        mock_image_data = MagicMock()
        mock_image_data.b64_json = fake_b64

        mock_response = MagicMock()
        mock_response.data = [mock_image_data]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        with patch("yoto_lib.image_providers.openai_provider.OpenAI", return_value=mock_client):
            from yoto_lib.image_providers.openai_provider import OpenAIProvider
            provider = OpenAIProvider()

        result = provider.generate("a cute cat", 1024, 1024)
        assert result == fake_png
        assert isinstance(result, bytes)


class TestGeminiProvider:
    def test_generate_returns_bytes(self, monkeypatch):
        """generate() returns image bytes from generate_images response."""
        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20

        mock_response = MagicMock()
        mock_response.generated_images = [MagicMock()]
        mock_response.generated_images[0].image.image_bytes = fake_image_bytes

        mock_client = MagicMock()
        mock_client.models.generate_images.return_value = mock_response

        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            result = provider.generate("a cute cat", 512, 512)

        assert result == fake_image_bytes
        assert isinstance(result, bytes)


class TestOpenAIProviderEdit:
    def test_edit_calls_api_with_image(self, monkeypatch):
        """edit() sends the source image to the OpenAI API and returns bytes."""
        import base64

        fake_result = base64.b64encode(b"fake edited png").decode()

        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json=fake_result)]

        mock_client = MagicMock()
        mock_client.images.edit.return_value = mock_response

        with patch("yoto_lib.image_providers.openai_provider.OpenAI", return_value=mock_client):
            from yoto_lib.image_providers.openai_provider import OpenAIProvider
            provider = OpenAIProvider()
            result = provider.edit(b"source image bytes", "extend the background", 638, 1011)

        assert result == b"fake edited png"
        mock_client.images.edit.assert_called_once()
        call_kwargs = mock_client.images.edit.call_args
        assert "extend the background" in str(call_kwargs)


class TestGeminiProviderEdit:
    def test_edit_calls_api_with_image(self, monkeypatch):
        """edit() sends source image to Gemini editing API and returns bytes."""
        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20

        mock_response = MagicMock()
        mock_response.generated_images = [MagicMock()]
        mock_response.generated_images[0].image.image_bytes = fake_image_bytes

        mock_client = MagicMock()
        mock_client.models.edit_image.return_value = mock_response

        # edit() now opens the image with PIL, so pass valid PNG bytes
        from PIL import Image as PILImage
        import io
        test_img = PILImage.new("RGB", (100, 100), color="green")
        buf = io.BytesIO()
        test_img.save(buf, format="PNG")
        test_png = buf.getvalue()

        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            result = provider.edit(test_png, "extend the background", 638, 1011)

        assert result == fake_image_bytes
        mock_client.models.edit_image.assert_called_once()


# ── TestOpenAINearestSize ────────────────────────────────────────────────────


class TestOpenAINearestSize:
    def test_exact_1024x1024(self):
        from yoto_lib.image_providers.openai_provider import _nearest_size
        assert _nearest_size(1024, 1024) == (1024, 1024)

    def test_nearest_portrait(self):
        from yoto_lib.image_providers.openai_provider import _nearest_size
        # 900x1500: dist to (1024,1024)=241000, dist to (1024,1536)=16492
        assert _nearest_size(900, 1500) == (1024, 1536)

    def test_nearest_landscape(self):
        from yoto_lib.image_providers.openai_provider import _nearest_size
        assert _nearest_size(1536, 800) == (1536, 1024)

    def test_small_dims_map_to_square(self):
        from yoto_lib.image_providers.openai_provider import _nearest_size
        assert _nearest_size(256, 256) == (1024, 1024)


# ── TestGeminiAspectRatio ────────────────────────────────────────────────────


class TestGeminiAspectRatio:
    def _make_provider_and_client(self):
        mock_response = MagicMock()
        mock_response.generated_images = [MagicMock()]
        mock_response.generated_images[0].image.image_bytes = b"fake"
        mock_client = MagicMock()
        mock_client.models.generate_images.return_value = mock_response
        return mock_client

    def test_square_gets_1_1(self):
        """512x512 → aspect_ratio='1:1'."""
        mock_client = self._make_provider_and_client()
        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            provider.generate("test", 512, 512)
        call_kwargs = mock_client.models.generate_images.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.aspect_ratio == "1:1"

    def test_portrait_gets_3_4(self):
        """768x1024 (ratio=0.75) → aspect_ratio='3:4'."""
        mock_client = self._make_provider_and_client()
        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            provider.generate("test", 768, 1024)
        call_kwargs = mock_client.models.generate_images.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.aspect_ratio == "3:4"

    def test_landscape_gets_16_9(self):
        """1920x1080 (ratio~1.78) → aspect_ratio='16:9'."""
        mock_client = self._make_provider_and_client()
        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            provider.generate("test", 1920, 1080)
        call_kwargs = mock_client.models.generate_images.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.aspect_ratio == "16:9"


# ── TestGeminiGenerateErrors ─────────────────────────────────────────────────


class TestGeminiGenerateErrors:
    def test_empty_generated_images_raises(self):
        """Empty generated_images list → RuntimeError."""
        mock_response = MagicMock()
        mock_response.generated_images = []
        mock_client = MagicMock()
        mock_client.models.generate_images.return_value = mock_response

        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            with pytest.raises(RuntimeError, match="No image"):
                provider.generate("test", 512, 512)

    def test_none_generated_images_raises(self):
        """None generated_images → RuntimeError."""
        mock_response = MagicMock()
        mock_response.generated_images = None
        mock_client = MagicMock()
        mock_client.models.generate_images.return_value = mock_response

        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            with pytest.raises((RuntimeError, TypeError)):
                provider.generate("test", 512, 512)


# ── TestGeminiEditMaskConstruction ───────────────────────────────────────────


class TestGeminiEditMask:
    def test_edit_sends_canvas_and_mask(self):
        """edit() sends two reference images and uses correct model/mode."""
        from PIL import Image as PILImage
        import io

        fake_result_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        mock_response = MagicMock()
        mock_response.generated_images = [MagicMock()]
        mock_response.generated_images[0].image.image_bytes = fake_result_bytes
        mock_client = MagicMock()
        mock_client.models.edit_image.return_value = mock_response

        test_img = PILImage.new("RGB", (100, 100), color="green")
        buf = io.BytesIO()
        test_img.save(buf, format="PNG")
        test_png = buf.getvalue()

        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            result = provider.edit(test_png, "extend bg", 638, 1011)

        assert result == fake_result_bytes
        call_kwargs = mock_client.models.edit_image.call_args
        assert call_kwargs.kwargs.get("model") == "imagen-3.0-capability-001"
        ref_images = call_kwargs.kwargs.get("reference_images", [])
        assert len(ref_images) == 2
        config = call_kwargs.kwargs.get("config")
        assert config.edit_mode == "EDIT_MODE_INPAINT_INSERTION"


# ── TestDallE2Provider ───────────────────────────────────────────────────────


class TestDallE2Provider:
    def test_generate_returns_bytes(self):
        """generate() returns PNG bytes decoded from b64_json response."""
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        fake_b64 = base64.b64encode(fake_png).decode()

        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json=fake_b64)]
        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        with patch("yoto_lib.image_providers.dalle2_provider.OpenAI", return_value=mock_client):
            from yoto_lib.image_providers.dalle2_provider import DallE2Provider
            provider = DallE2Provider()
            result = provider.generate("pixel art cat", 256, 256)

        assert result == fake_png

    def test_snaps_to_nearest_size(self):
        """Requested 300x300 → snaps to 256x256."""
        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json=base64.b64encode(b"x").decode())]
        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        with patch("yoto_lib.image_providers.dalle2_provider.OpenAI", return_value=mock_client):
            from yoto_lib.image_providers.dalle2_provider import DallE2Provider
            provider = DallE2Provider()
            provider.generate("test", 300, 300)

        call_kwargs = mock_client.images.generate.call_args
        assert call_kwargs.kwargs.get("size") == "256x256" or "256x256" in str(call_kwargs)

    def test_uses_min_dimension_for_square(self):
        """Requested 512x768 → uses min(512,768)=512 → 512x512."""
        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json=base64.b64encode(b"x").decode())]
        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        with patch("yoto_lib.image_providers.dalle2_provider.OpenAI", return_value=mock_client):
            from yoto_lib.image_providers.dalle2_provider import DallE2Provider
            provider = DallE2Provider()
            provider.generate("test", 512, 768)

        call_kwargs = mock_client.images.generate.call_args
        assert call_kwargs.kwargs.get("size") == "512x512" or "512x512" in str(call_kwargs)


# ── TestTogetherProvider ─────────────────────────────────────────────────────


class TestTogetherProvider:
    def test_generate_returns_bytes(self, monkeypatch):
        """generate() returns bytes decoded from API response."""
        monkeypatch.setenv("TOGETHER_AI_KEY", "test-key")
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        fake_b64 = base64.b64encode(fake_png).decode()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": [{"b64_json": fake_b64}]}

        with patch("yoto_lib.image_providers.together_provider.httpx.post", return_value=mock_response):
            from yoto_lib.image_providers.together_provider import TogetherProvider
            provider = TogetherProvider()
            result = provider.generate("pixel art", 256, 256)

        assert result == fake_png

    def test_missing_api_key_raises(self, monkeypatch):
        """No TOGETHER_AI_KEY → RuntimeError."""
        monkeypatch.delenv("TOGETHER_AI_KEY", raising=False)
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="TOGETHER_AI_KEY"):
            from yoto_lib.image_providers.together_provider import TogetherProvider
            TogetherProvider()

    def test_passes_dimensions(self, monkeypatch):
        """Width and height are passed in the API request body."""
        monkeypatch.setenv("TOGETHER_AI_KEY", "test-key")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": [{"b64_json": base64.b64encode(b"x").decode()}]}

        with patch("yoto_lib.image_providers.together_provider.httpx.post", return_value=mock_response) as mock_post:
            from yoto_lib.image_providers.together_provider import TogetherProvider
            provider = TogetherProvider()
            provider.generate("test", 512, 768)

        call_kwargs = mock_post.call_args
        json_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert json_body["width"] == 512
        assert json_body["height"] == 768


# ── TestRetroDiffusionProviderGenerate ───────────────────────────────────────


class TestRetroDiffusionProviderGenerate:
    def test_generate_delegates_to_batch(self, monkeypatch):
        """generate() calls generate_batch(count=1) and returns first result."""
        monkeypatch.setenv("RETRODIFFUSION_API_KEY", "test-key")
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"base64_images": [base64.b64encode(fake_png).decode()]}

        with patch("yoto_lib.image_providers.retrodiffusion_provider.httpx.post", return_value=mock_response):
            from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider
            provider = RetroDiffusionProvider()
            result = provider.generate("pixel cat", 16, 16)

        assert result == fake_png

    def test_missing_api_key_raises(self, monkeypatch):
        """No RETRODIFFUSION_API_KEY → RuntimeError."""
        monkeypatch.delenv("RETRODIFFUSION_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="RETRODIFFUSION_API_KEY"):
            from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider
            RetroDiffusionProvider()

    def test_batch_returns_multiple(self, monkeypatch):
        """generate_batch(count=3) returns 3 images."""
        monkeypatch.setenv("RETRODIFFUSION_API_KEY", "test-key")
        fake_pngs = [b"img1", b"img2", b"img3"]
        fake_b64s = [base64.b64encode(p).decode() for p in fake_pngs]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"base64_images": fake_b64s}

        with patch("yoto_lib.image_providers.retrodiffusion_provider.httpx.post", return_value=mock_response):
            from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider
            provider = RetroDiffusionProvider()
            results = provider.generate_batch("pixel cat", 16, 16, count=3)

        assert len(results) == 3
        assert results == fake_pngs
