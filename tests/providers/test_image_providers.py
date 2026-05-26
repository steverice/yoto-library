"""Tests for image provider interface and implementations."""

import base64
from unittest.mock import MagicMock, patch

from yoto_lib.providers import get_provider


class TestGetProvider:
    def test_returns_openai_provider(self):
        """get_provider returns an OpenAIProvider."""
        with patch("yoto_lib.providers.openai_provider.OpenAI"):
            provider = get_provider()
        from yoto_lib.providers.openai_provider import OpenAIProvider

        assert isinstance(provider, OpenAIProvider)


class TestOpenAIProvider:
    def test_generate_returns_bytes(self):
        """generate() returns PNG bytes decoded from b64_json response."""
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        fake_b64 = base64.b64encode(fake_png).decode()

        mock_image_data = MagicMock()
        mock_image_data.b64_json = fake_b64

        mock_response = MagicMock()
        mock_response.data = [mock_image_data]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        with patch("yoto_lib.providers.openai_provider.OpenAI", return_value=mock_client):
            from yoto_lib.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider()

        result = provider.generate("a cute cat", 1024, 1024)
        assert result == fake_png
        assert isinstance(result, bytes)


def test_openai_generate_passes_quality(mock_openai_client):
    """OpenAIProvider.generate() passes quality to the API."""
    from yoto_lib.providers.openai_provider import OpenAIProvider

    with (
        patch("yoto_lib.providers.openai_provider.OpenAI", return_value=mock_openai_client),
        patch(
            "yoto_lib.billing.costs.COSTS",
            {
                "openai_generate_low": {"cost": 0.016, "label": "OpenAI generation (low)"},
            },
        ),
    ):
        provider = OpenAIProvider()
        provider.generate("test prompt", 1024, 1536, quality="low")

    mock_openai_client.images.generate.assert_called_once_with(
        model="gpt-image-1.5",
        prompt="test prompt",
        size="1024x1536",
        quality="low",
    )


def test_openai_edit_passes_quality(mock_openai_client):
    """OpenAIProvider.edit() passes quality to the API."""
    from yoto_lib.providers.openai_provider import OpenAIProvider

    fake_image = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    fake_mask = b""

    with (
        patch("yoto_lib.providers.openai_provider.OpenAI", return_value=mock_openai_client),
        patch(
            "yoto_lib.billing.costs.COSTS",
            {
                "openai_edit_high": {"cost": 0.08, "label": "OpenAI edit (high)"},
            },
        ),
    ):
        provider = OpenAIProvider()
        provider.edit(fake_image, fake_mask, "test prompt", 1024, 1024, quality="high")

    call_kwargs = mock_openai_client.images.edit.call_args[1]
    assert call_kwargs.get("quality") == "high"
    assert call_kwargs.get("model") == "gpt-image-1.5"
    assert call_kwargs.get("size") == "1024x1024"


def test_openai_edit_passes_input_fidelity(mock_openai_client):
    """OpenAIProvider.edit() forwards input_fidelity when given, and omits it by default."""
    from yoto_lib.providers.openai_provider import OpenAIProvider

    fake_image = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20

    with patch("yoto_lib.providers.openai_provider.OpenAI", return_value=mock_openai_client):
        provider = OpenAIProvider()
        provider.edit(fake_image, b"", "test prompt", 1024, 1024, input_fidelity="high")
        high_kwargs = mock_openai_client.images.edit.call_args[1]

        provider.edit(fake_image, b"", "test prompt", 1024, 1024)
        default_kwargs = mock_openai_client.images.edit.call_args[1]

    assert high_kwargs.get("input_fidelity") == "high"
    assert "input_fidelity" not in default_kwargs


class TestGeminiProvider:
    @staticmethod
    def _mock_client_returning_png():
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        part = MagicMock()
        part.inline_data.data = png
        candidate = MagicMock()
        candidate.finish_reason = "STOP"
        candidate.content.parts = [part]
        response = MagicMock()
        response.candidates = [candidate]
        client = MagicMock()
        client.models.generate_content.return_value = response
        return client, png

    def test_generate_forwards_aspect_ratio(self):
        """aspect_ratio is passed through image_config to the Gemini API."""
        client, png = self._mock_client_returning_png()
        with patch("google.genai.Client", return_value=client):
            from yoto_lib.providers.gemini_provider import GeminiProvider

            result = GeminiProvider().generate("a cover", aspect_ratio="2:3")

        assert result == png
        config = client.models.generate_content.call_args.kwargs["config"]
        assert config.image_config is not None
        assert config.image_config.aspect_ratio == "2:3"

    def test_generate_without_aspect_ratio_omits_image_config(self):
        """Default generation sends no image_config (regression for existing callers)."""
        client, _ = self._mock_client_returning_png()
        with patch("google.genai.Client", return_value=client):
            from yoto_lib.providers.gemini_provider import GeminiProvider

            GeminiProvider().generate("a cover")

        config = client.models.generate_content.call_args.kwargs["config"]
        assert config.image_config is None


class TestTogetherAIProvider:
    def test_recompose_uploads_and_returns_bytes(self):
        """recompose() uploads padded image and returns FLUX result."""
        import io

        from PIL import Image as PILImage

        # Create a valid PNG for the fake FLUX response
        fake_img = PILImage.new("RGB", (800, 1328), color="blue")
        fake_buf = io.BytesIO()
        fake_img.save(fake_buf, format="PNG")
        fake_png = fake_buf.getvalue()
        fake_result = base64.b64encode(fake_png).decode()

        mock_response = MagicMock()
        mock_response.data = [MagicMock(b64_json=fake_result)]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        with patch("yoto_lib.providers.together_provider.Together", return_value=mock_client):
            from yoto_lib.providers.together_provider import TogetherAIProvider

            provider = TogetherAIProvider()
            import io

            from PIL import Image as PILImage

            test_img = PILImage.new("RGB", (100, 100), color="green")
            buf = io.BytesIO()
            test_img.save(buf, format="PNG")

            result = provider.recompose(buf.getvalue(), "test prompt", 638, 1011)

        assert result == fake_png
        mock_client.images.generate.assert_called_once()
        call_kwargs = mock_client.images.generate.call_args
        assert "FLUX.1-kontext-pro" in str(call_kwargs)
