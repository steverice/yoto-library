"""Together AI image provider — supports multiple models with small size output."""
import base64
import os

import httpx


class TogetherProvider:
    """Generates images via Together AI. Supports small output sizes."""

    def __init__(self, model: str = "black-forest-labs/FLUX.1-schnell") -> None:
        self._api_key = os.environ.get("TOGETHER_AI_KEY") or os.environ.get("TOGETHER_API_KEY")
        if not self._api_key:
            raise RuntimeError("TOGETHER_AI_KEY not set")
        self._model = model

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image. Returns PNG bytes."""
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
        return base64.b64decode(b64_data)
