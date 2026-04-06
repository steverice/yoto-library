"""FLUX image provider via Together AI."""
import base64
import logging
import tempfile
from pathlib import Path

import requests
from together import Together

logger = logging.getLogger(__name__)

# Temporary file hosting for image upload (Together AI requires HTTP URLs)
_TMPFILES_UPLOAD = "https://tmpfiles.org/api/v1/upload"


def _upload_temp(image_bytes: bytes) -> str:
    """Upload image bytes to tmpfiles.org, return a direct-download URL."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(image_bytes)
        tmp_path = Path(f.name)
    try:
        resp = requests.post(
            _TMPFILES_UPLOAD,
            files={"file": ("image.png", open(tmp_path, "rb"), "image/png")},
            timeout=30,
        )
        resp.raise_for_status()
        url = resp.json()["data"]["url"]
        # Convert page URL to direct download URL
        return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    finally:
        tmp_path.unlink(missing_ok=True)


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
            response_format="base64",
        )

        result = base64.b64decode(response.data[0].b64_json)
        logger.debug("flux: generated %d bytes", len(result))
        return result

    def recompose(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Recompose an image using FLUX Kontext. Returns image bytes."""
        w = round(width / 16) * 16
        h = round(height / 16) * 16
        logger.debug("flux: recomposing %dx%d, prompt=%.80s...", w, h, prompt)

        image_url = _upload_temp(image)
        logger.debug("flux: uploaded source image to %s", image_url)

        response = self._client.images.generate(
            model="black-forest-labs/FLUX.1-kontext-pro",
            prompt=prompt,
            image_url=image_url,
            width=w,
            height=h,
            steps=28,
            response_format="base64",
        )

        result = base64.b64decode(response.data[0].b64_json)
        logger.debug("flux: recomposed %d bytes", len(result))
        return result
