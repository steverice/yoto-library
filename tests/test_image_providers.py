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

        with patch("yoto_lib.image_providers.gemini_provider.genai.Client", return_value=mock_client):
            from yoto_lib.image_providers.gemini_provider import GeminiProvider
            provider = GeminiProvider()
            result = provider.edit(b"source image", "extend the background", 638, 1011)

        assert result == fake_image_bytes
        mock_client.models.edit_image.assert_called_once()
