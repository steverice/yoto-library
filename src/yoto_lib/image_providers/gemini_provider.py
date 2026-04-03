"""Gemini image provider implementation."""
from google import genai
from google.genai import types


class GeminiProvider:
    """Generates images using the Gemini generative AI API."""

    def __init__(self) -> None:
        self._client = genai.Client()

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns image bytes."""
        response = self._client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                return part.inline_data.data

        raise RuntimeError("No image found in Gemini response")
