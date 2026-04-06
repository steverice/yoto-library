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
        """Generate an image from a text prompt."""
        # Pick the closest standard aspect ratio to the requested dimensions
        target = width / height
        options = [("1:1", 1.0), ("9:16", 9/16), ("16:9", 16/9),
                   ("3:4", 3/4), ("4:3", 4/3)]
        aspect_ratio = min(options, key=lambda x: abs(x[1] - target))[0]
        logger.debug("gemini: generating, aspect=%s prompt=%.80s...", aspect_ratio, prompt)
        response = self._client.models.generate_images(
            model="imagen-4.0-fast-generate-001",
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
            ),
        )

        if response.generated_images:
            result = response.generated_images[0].image.image_bytes
            logger.debug("gemini: generated %d bytes", len(result))
            return result

        raise RuntimeError("No image found in Gemini response")

    def edit(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Edit an image using Gemini's image editing API. Returns PNG bytes."""
        target = width / height
        options = [("1:1", 1.0), ("9:16", 9/16), ("16:9", 16/9),
                   ("3:4", 3/4), ("4:3", 4/3)]
        aspect_ratio = min(options, key=lambda x: abs(x[1] - target))[0]
        logger.debug("gemini: editing, aspect=%s prompt=%.80s...", aspect_ratio, prompt)

        response = self._client.models.edit_image(
            model="imagen-3.0-capability-001",
            prompt=prompt,
            reference_images=[types.RawReferenceImage(
                reference_image=types.Image(image_bytes=image, mime_type="image/png"),
                reference_id=0,
            )],
            config=types.EditImageConfig(
                edit_mode="EDIT_MODE_OUTPAINT",
                number_of_images=1,
                aspect_ratio=aspect_ratio,
            ),
        )

        if response.generated_images:
            result = response.generated_images[0].image.image_bytes
            logger.debug("gemini: edited %d bytes", len(result))
            return result

        raise RuntimeError("No image found in Gemini edit response")
