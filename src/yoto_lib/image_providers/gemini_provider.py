"""Gemini image provider implementation."""
import logging

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class GeminiProvider:
    """Generates images using the Gemini Imagen API."""

    def __init__(self) -> None:
        self._client: genai.Client | None = None
        self._vertex_client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        """Lazy AI Studio client (requires GEMINI_API_KEY)."""
        if self._client is None:
            self._client = genai.Client()
        return self._client

    def _get_vertex_client(self) -> genai.Client:
        """Lazy Vertex AI client (requires gcloud auth)."""
        if self._vertex_client is None:
            self._vertex_client = genai.Client(vertexai=True)
        return self._vertex_client

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt."""
        # Pick the closest standard aspect ratio to the requested dimensions
        target = width / height
        options = [("1:1", 1.0), ("9:16", 9/16), ("16:9", 16/9),
                   ("3:4", 3/4), ("4:3", 4/3)]
        aspect_ratio = min(options, key=lambda x: abs(x[1] - target))[0]
        logger.debug("gemini: generating, aspect=%s prompt=%.80s...", aspect_ratio, prompt)
        response = self._get_client().models.generate_images(
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

    def recompose(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Recompose an image into new dimensions using Gemini multimodal generation.

        Sends the source image to Gemini's generate_content API along with a
        text prompt, and asks it to create a new image inspired by the original.
        """
        target = width / height
        options = [("1:1", 1.0), ("9:16", 9/16), ("16:9", 16/9),
                   ("2:3", 2/3), ("3:2", 3/2), ("3:4", 3/4), ("4:3", 4/3)]
        aspect_ratio = min(options, key=lambda x: abs(x[1] - target))[0]
        logger.debug("gemini: recomposing to %s, prompt=%.80s...", aspect_ratio, prompt)

        response = self._get_client().models.generate_content(
            model="gemini-2.5-flash-image",
            contents=[prompt, types.Part.from_bytes(data=image, mime_type="image/png")],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                result = part.inline_data.data
                logger.debug("gemini: recomposed %d bytes", len(result))
                return result

        raise RuntimeError("No image found in Gemini recompose response")
