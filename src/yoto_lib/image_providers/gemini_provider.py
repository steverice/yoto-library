"""Gemini image provider implementation."""
import logging

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class GeminiProvider:
    """Generates images using the Gemini Imagen API."""

    def __init__(self) -> None:
        self._client = genai.Client()

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns image bytes (always 1024x1024)."""
        logger.debug("gemini: generating, prompt=%.80s...", prompt)
        response = self._client.models.generate_images(
            model="imagen-4.0-fast-generate-001",
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
            ),
        )

        if response.generated_images:
            result = response.generated_images[0].image.image_bytes
            logger.debug("gemini: generated %d bytes", len(result))
            return result

        raise RuntimeError("No image found in Gemini response")
