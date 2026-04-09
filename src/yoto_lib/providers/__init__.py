"""Image provider interface and factory."""
from __future__ import annotations

from typing import Protocol


class ImageProvider(Protocol):
    """Protocol for image generation providers."""

    def generate(self, prompt: str, width: int, height: int, **kwargs: object) -> bytes:
        """Generate an image from a text prompt. Returns PNG bytes."""
        ...


from yoto_lib.providers.openai_provider import OpenAIProvider


def get_provider() -> OpenAIProvider:
    """Return the OpenAI image provider for text-to-image cover generation."""
    return OpenAIProvider()
