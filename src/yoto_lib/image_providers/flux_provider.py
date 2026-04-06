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

        Pads the source image to portrait dimensions using the average edge
        color, then asks FLUX Kontext to recompose the scene for the taller
        frame. Edge-color padding blends naturally with the artwork, encouraging
        FLUX to extend the scene rather than treating the padding as solid bars.
        """
        from yoto_lib.cover import pad_to_cover
        padded_bytes = pad_to_cover(image, width, height)
        padded_b64 = base64.b64encode(padded_bytes).decode()
        data_uri = f"data:image/png;base64,{padded_b64}"

        logger.debug("flux: recomposing with kontext, canvas=%dx%d", width, height)

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
