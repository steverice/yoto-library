"""Image provider interface and factory."""
import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class ImageProvider(Protocol):
    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns PNG bytes."""
        ...

    def edit(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Edit/outpaint an image. Returns image bytes."""
        ...


def get_provider() -> ImageProvider:
    """Return the configured ImageProvider based on YOTO_IMAGE_PROVIDER env var."""
    provider_name = os.environ.get("YOTO_IMAGE_PROVIDER", "openai")

    if provider_name == "openai":
        from yoto_lib.image_providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    elif provider_name == "gemini":
        from yoto_lib.image_providers.gemini_provider import GeminiProvider
        return GeminiProvider()
    else:
        raise ValueError(f"Unknown image provider: {provider_name!r}")
