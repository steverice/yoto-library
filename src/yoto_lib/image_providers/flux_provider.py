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
        """Recompose an image using FLUX Kontext targeted fill.

        Pads the source image to the target dimensions with black bars, then
        asks FLUX Kontext to fill the black areas with contextually appropriate
        content. Kontext preserves the original art and fills the new regions.
        """
        import io
        from PIL import Image as PILImage

        # Build a padded canvas: original art centered, black bars fill the rest
        art = PILImage.open(io.BytesIO(image)).convert("RGB")
        scale = min(width / art.width, height / art.height)
        new_w = int(art.width * scale)
        new_h = int(art.height * scale)
        scaled = art.resize((new_w, new_h), PILImage.LANCZOS)
        canvas = PILImage.new("RGB", (width, height), (0, 0, 0))
        x_off = (width - new_w) // 2
        y_off = (height - new_h) // 2
        canvas.paste(scaled, (x_off, y_off))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        padded_bytes = buf.getvalue()

        logger.debug("flux: recomposing with kontext fill, canvas=%dx%d", width, height)
        image_url = _upload_temp(padded_bytes)
        logger.debug("flux: uploaded padded canvas to %s", image_url)

        response = self._client.images.generate(
            model="black-forest-labs/FLUX.1-kontext-pro",
            prompt=prompt,
            image_url=image_url,
            steps=28,
            response_format="base64",
        )

        result = base64.b64decode(response.data[0].b64_json)
        with PILImage.open(io.BytesIO(result)) as img:
            logger.debug("flux: recomposed %d bytes, size=%dx%d", len(result), img.width, img.height)
        return result
