"""Tests for image provider interface and implementations."""
import base64
from unittest.mock import MagicMock, patch

from yoto_lib.image_providers import get_provider


class TestGetProvider:
    def test_returns_openai_provider(self):
        """get_provider returns an OpenAIProvider."""
        with patch("yoto_lib.image_providers.openai_provider.OpenAI"):
            provider = get_provider()
        from yoto_lib.image_providers.openai_provider import OpenAIProvider
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

        with patch("yoto_lib.image_providers.openai_provider.OpenAI", return_value=mock_client):
            from yoto_lib.image_providers.openai_provider import OpenAIProvider
            provider = OpenAIProvider()

        result = provider.generate("a cute cat", 1024, 1024)
        assert result == fake_png
        assert isinstance(result, bytes)


class TestFluxProvider:
    def test_recompose_uploads_and_returns_bytes(self):
        """recompose() uploads padded image and returns FLUX result."""
        from PIL import Image as PILImage
        import io

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

        with (
            patch("yoto_lib.image_providers.flux_provider.Together", return_value=mock_client),
            patch("yoto_lib.image_providers.flux_provider._upload_temp", return_value="http://example.com/img.png"),
        ):
            from yoto_lib.image_providers.flux_provider import FluxProvider
            provider = FluxProvider()
            from PIL import Image as PILImage
            import io
            test_img = PILImage.new("RGB", (100, 100), color="green")
            buf = io.BytesIO()
            test_img.save(buf, format="PNG")

            result = provider.recompose(buf.getvalue(), "test prompt", 638, 1011)

        assert result == fake_png
        mock_client.images.generate.assert_called_once()
        call_kwargs = mock_client.images.generate.call_args
        assert "FLUX.1-kontext-pro" in str(call_kwargs)
