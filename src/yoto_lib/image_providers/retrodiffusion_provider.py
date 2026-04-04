"""Retro Diffusion image provider — purpose-built for pixel art, supports 16x16."""
import base64
import os

import httpx


class RetroDiffusionProvider:
    """Generates pixel art using Retro Diffusion API."""

    def __init__(self, style: str = "rd_fast__low_res") -> None:
        self._api_key = os.environ.get("RETRODIFFUSION_API_KEY")
        if not self._api_key:
            raise RuntimeError("RETRODIFFUSION_API_KEY not set")
        self._style = style

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate pixel art. Returns PNG bytes at the exact requested size."""
        response = httpx.post(
            "https://api.retrodiffusion.ai/v1/inferences",
            headers={"X-RD-Token": self._api_key},
            json={
                "prompt": prompt,
                "width": width,
                "height": height,
                "num_images": 1,
                "prompt_style": self._style,
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        b64_data = data["base64_images"][0]
        return base64.b64decode(b64_data)
