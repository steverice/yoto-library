"""Retro Diffusion image provider — purpose-built for pixel art, supports 16x16."""

from __future__ import annotations

import base64
import logging
import os

import httpx

from yoto_lib.providers.base import ImageProvider

logger = logging.getLogger(__name__)


class RetroDiffusionProvider(ImageProvider):
    """Generates pixel art using Retro Diffusion API."""

    display_name = "RetroDiffusion"

    def __init__(self, style: str = "rd_fast__low_res") -> None:
        self._api_key = os.environ.get("RETRODIFFUSION_API_KEY")
        if not self._api_key:
            raise RuntimeError("RETRODIFFUSION_API_KEY not set")
        self._style = style

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate pixel art. Returns PNG bytes at the exact requested size."""
        return self.generate_batch(prompt, width, height, count=1)[0]

    def generate_batch(
        self,
        prompt: str,
        width: int,
        height: int,
        count: int = 1,
        *,
        input_image: bytes | None = None,
        strength: float = 0.75,
    ) -> list[bytes]:
        """Generate multiple pixel art images. Returns list of PNG bytes.

        When ``input_image`` (already PNG-encoded RGB bytes) is supplied, the
        request runs as img2img: the reference loosely informs the result while
        ``strength`` controls how much the prompt overrides it (0 = reference
        preserved, 1 = reference ignored).
        """
        logger.debug("retrodiffusion: generating %dx%d x%d, prompt=%.80s...", width, height, count, prompt)
        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_images": count,
            "prompt_style": self._style,
        }
        if input_image is not None:
            payload["input_image"] = base64.b64encode(input_image).decode("utf-8")
            payload["strength"] = strength
        response = httpx.post(
            "https://api.retrodiffusion.ai/v1/inferences",
            headers={"X-RD-Token": self._api_key},  # ty: ignore[invalid-argument-type]
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        images = [base64.b64decode(b64) for b64 in data["base64_images"]]
        logger.debug("retrodiffusion: generated %d images", len(images))
        from yoto_lib.billing.costs import get_tracker

        get_tracker().record("retrodiffusion", count=len(images))
        return images
