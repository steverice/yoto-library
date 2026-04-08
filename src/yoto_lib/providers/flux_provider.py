"""FLUX image provider via Together AI."""
import base64
import io
import logging

from PIL import Image as PILImage
from together import Together

from yoto_lib.providers.base import Provider, ProviderStatus

logger = logging.getLogger(__name__)


class FluxProvider(Provider):
    """Generates and recomposes images using FLUX models on Together AI."""

    @classmethod
    def check_status(cls) -> ProviderStatus:
        return ProviderStatus(healthy=True)

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
        from yoto_lib.billing.costs import get_tracker
        get_tracker().record("flux_generate")
        return result

    def recompose(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Recompose an image using FLUX Kontext.

        Pads the source image onto a black canvas at the target dimensions,
        then asks FLUX Kontext to outpaint the scene into the black areas.
        Black padding is the standard approach for FLUX outpainting.
        """
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
        padded_b64 = base64.b64encode(buf.getvalue()).decode()
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
        from yoto_lib.billing.costs import get_tracker
        get_tracker().record("flux_recompose")
        return result
