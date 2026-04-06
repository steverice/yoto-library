"""Image provider interface and factory."""
from yoto_lib.image_providers.openai_provider import OpenAIProvider


def get_provider() -> OpenAIProvider:
    """Return the OpenAI image provider for text-to-image cover generation."""
    return OpenAIProvider()
