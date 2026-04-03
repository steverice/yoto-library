"""Gemini image provider implementation."""
import google.generativeai as genai


class GeminiProvider:
    """Generates images using the Gemini generative AI API."""

    def __init__(self) -> None:
        self._model = genai.GenerativeModel("gemini-2.0-flash-exp")

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns image bytes."""
        response = self._model.generate_content(prompt)

        for candidate in response.candidates:
            for part in candidate.content.parts:
                mime = part.inline_data.mime_type
                if mime.startswith("image/"):
                    return part.inline_data.data

        raise RuntimeError("No image found in Gemini response")
