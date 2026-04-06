"""FLUX image provider via Together AI."""
import base64
import logging

import requests
from together import Together

logger = logging.getLogger(__name__)


class FluxProvider:
    """Generates and recomposes images using FLUX models on Together AI."""

    def __init__(self) -> None:
        self._client = Together()

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns PNG bytes."""
        # Round to nearest multiple of 16
        w = round(width / 16) * 16
        h = round(height / 16) * 16
        logger.debug("flux: generating %dx%d, prompt=%.80s...", w, h, prompt)

        response = self._client.images.generate(
            model="black-forest-labs/FLUX.1.1-pro",
            prompt=prompt,
            width=w,
            height=h,
            steps=28,
        )

        url = response.data[0].url
        img_bytes = requests.get(url, timeout=30).content
        logger.debug("flux: generated %d bytes", len(img_bytes))
        return img_bytes

    def recompose(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Recompose an image using FLUX Kontext. Returns image bytes."""
        w = round(width / 16) * 16
        h = round(height / 16) * 16
        logger.debug("flux: recomposing %dx%d, prompt=%.80s...", w, h, prompt)

        b64 = base64.b64encode(image).decode()
        data_uri = f"data:image/png;base64,{b64}"

        response = self._client.images.generate(
            model="black-forest-labs/FLUX.1-kontext-pro",
            prompt=prompt,
            image_url=data_uri,
            width=w,
            height=h,
            steps=28,
        )

        url = response.data[0].url
        img_bytes = requests.get(url, timeout=30).content
        logger.debug("flux: recomposed %d bytes", len(img_bytes))
        return img_bytes
