"""OpenAI image provider implementation."""
import base64
import logging

from openai import OpenAI

logger = logging.getLogger(__name__)


# Supported sizes for gpt-image-1
_SUPPORTED_SIZES = [
    (1024, 1024),
    (1024, 1536),
    (1536, 1024),
]


def _nearest_size(width: int, height: int) -> tuple[int, int]:
    """Map requested dimensions to the nearest supported size."""
    def _distance(size: tuple[int, int]) -> float:
        sw, sh = size
        return (sw - width) ** 2 + (sh - height) ** 2

    return min(_SUPPORTED_SIZES, key=_distance)


class OpenAIProvider:
    """Generates images using the OpenAI images API (gpt-image-1)."""

    def __init__(self) -> None:
        self._client = OpenAI()

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns PNG bytes."""
        nearest_w, nearest_h = _nearest_size(width, height)
        size_str = f"{nearest_w}x{nearest_h}"
        logger.debug("openai: generating %s, prompt=%.80s...", size_str, prompt)

        response = self._client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size=size_str,
            quality="medium",
        )

        b64_data = response.data[0].b64_json
        result = base64.b64decode(b64_data)
        logger.debug("openai: generated %d bytes", len(result))
        return result

    def edit(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Edit an image using OpenAI's image editing API. Returns PNG bytes."""
        import io

        nearest_w, nearest_h = _nearest_size(width, height)
        size_str = f"{nearest_w}x{nearest_h}"
        logger.debug("openai: editing %s, prompt=%.80s...", size_str, prompt)

        buf = io.BytesIO(image)
        buf.name = "image.png"

        response = self._client.images.edit(
            model="gpt-image-1",
            image=buf,
            prompt=prompt,
            size=size_str,
        )

        b64_data = response.data[0].b64_json
        result = base64.b64decode(b64_data)
        logger.debug("openai: edited %d bytes", len(result))
        return result
