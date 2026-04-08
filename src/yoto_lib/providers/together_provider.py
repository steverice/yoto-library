"""Together AI image provider — supports multiple models with small size output."""
from __future__ import annotations

import base64
import logging
import os

import httpx

from yoto_lib.providers.base import Provider, ProviderStatus

logger = logging.getLogger(__name__)


class TogetherProvider(Provider):
    """Generates images via Together AI. Supports small output sizes."""

    @classmethod
    def check_status(cls) -> ProviderStatus:
        return ProviderStatus(healthy=True)

    def __init__(self, model: str = "black-forest-labs/FLUX.1-schnell") -> None:
        self._api_key = os.environ.get("TOGETHER_AI_KEY") or os.environ.get("TOGETHER_API_KEY")
        if not self._api_key:
            raise RuntimeError("TOGETHER_AI_KEY not set")
        self._model = model

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image. Returns PNG bytes."""
        logger.debug("together (%s): generating %dx%d, prompt=%.80s...", self._model, width, height, prompt)
        response = httpx.post(
            "https://api.together.xyz/v1/images/generations",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "prompt": prompt,
                "width": width,
                "height": height,
                "n": 1,
                "response_format": "b64_json",
            },
            timeout=60,
        )
        response.raise_for_status()
        b64_data = response.json()["data"][0]["b64_json"]
        result = base64.b64decode(b64_data)
        logger.debug("together: generated %d bytes", len(result))
        return result
