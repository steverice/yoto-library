"""DALL-E 2 image provider — supports 256x256 output."""
import base64
import logging

from openai import OpenAI

logger = logging.getLogger(__name__)


_SUPPORTED_SIZES = {256, 512, 1024}


class DallE2Provider:
    """Generates images using DALL-E 2 (supports small sizes)."""

    def __init__(self) -> None:
        self._client = OpenAI()

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image. Returns PNG bytes."""
        # DALL-E 2 only supports square images
        size = min(width, height)
        # Snap to nearest supported size
        size = min(_SUPPORTED_SIZES, key=lambda s: abs(s - size))
        size_str = f"{size}x{size}"
        logger.debug("dalle2: generating %s, prompt=%.80s...", size_str, prompt)

        response = self._client.images.generate(
            model="dall-e-2",
            prompt=prompt,
            size=size_str,
            response_format="b64_json",
        )

        b64_data = response.data[0].b64_json
        result = base64.b64decode(b64_data)
        logger.debug("dalle2: generated %d bytes", len(result))
        return result
