"""Google Gemini image provider."""
from __future__ import annotations

import logging

from yoto_lib.providers.base import Provider

logger = logging.getLogger(__name__)


class GeminiProvider(Provider):
    """Generates images using Google Gemini."""

    MODEL = "gemini-2.5-flash-image"

    def generate(self, prompt: str, reference_image: bytes | None = None) -> bytes:
        """Generate an image from a text prompt.

        Args:
            prompt: Text describing the image to generate.
            reference_image: Optional PNG bytes shown to the model as context.

        Returns PNG bytes. Raises RuntimeError if the model returns no image.
        """
        from google import genai
        from google.genai import types

        client = genai.Client()

        contents: list = [prompt]
        if reference_image is not None:
            contents.append(types.Part.from_bytes(data=reference_image, mime_type="image/png"))

        logger.debug("gemini: generating, model=%s, prompt=%.80s...", self.MODEL, prompt)

        response = client.models.generate_content(
            model=self.MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        if not response.candidates:
            feedback = getattr(response, "prompt_feedback", None)
            raise RuntimeError(f"Gemini returned no candidates (feedback={feedback})")

        candidate = response.candidates[0]
        finish = getattr(candidate, "finish_reason", None)
        if finish and str(finish) not in ("STOP", "0", "FinishReason.STOP"):
            logger.warning("gemini: finish_reason=%s", finish)

        if not candidate.content or not candidate.content.parts:
            raise RuntimeError("Gemini response has empty content")

        for part in candidate.content.parts:
            if part.inline_data is not None:
                logger.debug("gemini: generated %d bytes", len(part.inline_data.data))
                from yoto_lib.billing.costs import get_tracker
                get_tracker().record("gemini_flash_image")
                return part.inline_data.data
            if hasattr(part, "text") and part.text:
                logger.debug("gemini: got text instead of image: %s", part.text[:200])

        raise RuntimeError("Gemini response contained no image data")
