"""Image provider interface and factory."""

from __future__ import annotations

import os


def get_provider_classes() -> list[tuple[type, str | None]]:
    """Return all provider classes with their env var requirements.

    Returns list of (provider_class, env_var_name) tuples.
    env_var is None for providers that are always available (e.g. Claude CLI).
    """
    from yoto_lib.providers.claude_provider import ClaudeProvider
    from yoto_lib.providers.gemini_provider import GeminiProvider
    from yoto_lib.providers.openai_provider import OpenAIProvider
    from yoto_lib.providers.retrodiffusion_provider import RetroDiffusionProvider
    from yoto_lib.providers.together_provider import TogetherAIProvider

    return [
        (RetroDiffusionProvider, "RETRODIFFUSION_API_KEY"),
        (OpenAIProvider, "OPENAI_API_KEY"),
        (TogetherAIProvider, "TOGETHER_API_KEY"),
        (GeminiProvider, "GEMINI_API_KEY"),
        (ClaudeProvider, None),
    ]


def get_active_providers() -> list[type]:
    """Return provider classes whose API keys are configured (or always available)."""
    return [cls for cls, env_var in get_provider_classes() if env_var is None or os.environ.get(env_var)]


def get_provider():
    """Return the OpenAI image provider for text-to-image cover generation."""
    from yoto_lib.providers.openai_provider import OpenAIProvider

    return OpenAIProvider()
