"""FLUX image provider via Together AI."""
import base64
import io
import logging

from PIL import Image as PILImage
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
            response_format="base64",
        )

        result = base64.b64decode(response.data[0].b64_json)
        logger.debug("flux: generated %d bytes", len(result))
        return result

    def recompose(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Recompose an image using FLUX Kontext.

        Pads the source image to portrait dimensions with black bars, then
        asks FLUX Kontext to recompose the scene for the taller frame.
        """
        # Round to multiples of 16 (FLUX requirement)
        w = round(width / 16) * 16
        h = round(height / 16) * 16

        # Build a padded canvas: original art centered, black bars fill the rest
        art = PILImage.open(io.BytesIO(image)).convert("RGB")
        scale = min(w / art.width, h / art.height)
        new_w = int(art.width * scale)
        new_h = int(art.height * scale)
        scaled = art.resize((new_w, new_h), PILImage.LANCZOS)
        canvas = PILImage.new("RGB", (w, h), (0, 0, 0))
        x_off = (w - new_w) // 2
        y_off = (h - new_h) // 2
        canvas.paste(scaled, (x_off, y_off))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        padded_b64 = base64.b64encode(buf.getvalue()).decode()
        data_uri = f"data:image/png;base64,{padded_b64}"

        logger.debug("flux: recomposing with kontext, canvas=%dx%d", w, h)

        response = self._client.images.generate(
            model="black-forest-labs/FLUX.1-kontext-pro",
            prompt=prompt,
            image_url=data_uri,
            steps=28,
            response_format="base64",
        )

        result = base64.b64decode(response.data[0].b64_json)
        with PILImage.open(io.BytesIO(result)) as img:
            logger.debug("flux: recomposed %d bytes, size=%dx%d", len(result), img.width, img.height)
        return result
