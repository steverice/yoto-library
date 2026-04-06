"""Gemini image provider implementation."""
import io
import logging

from PIL import Image

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class GeminiProvider:
    """Generates images using the Gemini Imagen API."""

    def __init__(self) -> None:
        self._client: genai.Client | None = None
        self._vertex_client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        """Lazy AI Studio client (requires GEMINI_API_KEY)."""
        if self._client is None:
            self._client = genai.Client()
        return self._client

    def _get_vertex_client(self) -> genai.Client:
        """Lazy Vertex AI client (requires gcloud auth)."""
        if self._vertex_client is None:
            self._vertex_client = genai.Client(vertexai=True)
        return self._vertex_client

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt."""
        # Pick the closest standard aspect ratio to the requested dimensions
        target = width / height
        options = [("1:1", 1.0), ("9:16", 9/16), ("16:9", 16/9),
                   ("3:4", 3/4), ("4:3", 4/3)]
        aspect_ratio = min(options, key=lambda x: abs(x[1] - target))[0]
        logger.debug("gemini: generating, aspect=%s prompt=%.80s...", aspect_ratio, prompt)
        response = self._get_client().models.generate_images(
            model="imagen-4.0-fast-generate-001",
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
            ),
        )

        if response.generated_images:
            result = response.generated_images[0].image.image_bytes
            logger.debug("gemini: generated %d bytes", len(result))
            return result

        raise RuntimeError("No image found in Gemini response")

    def edit(self, image: bytes, prompt: str, width: int, height: int) -> bytes:
        """Edit an image using Gemini Vertex AI inpainting. Returns PNG bytes.

        Places the source image centered on a canvas of the target dimensions,
        creates a mask for the padding areas, and asks Imagen to fill them in.
        """
        logger.debug("gemini: editing to %dx%d, prompt=%.80s...", width, height, prompt)

        art = Image.open(io.BytesIO(image)).convert("RGB")

        # Scale art to fit within target dimensions
        scale = min(width / art.width, height / art.height)
        new_w = int(art.width * scale)
        new_h = int(art.height * scale)
        scaled = art.resize((new_w, new_h), Image.LANCZOS)

        # Place on target-sized canvas
        canvas = Image.new("RGB", (width, height), (0, 0, 0))
        x_off = (width - new_w) // 2
        y_off = (height - new_h) // 2
        canvas.paste(scaled, (x_off, y_off))

        # Mask: white = fill (padding), black = keep (art)
        mask = Image.new("L", (width, height), 255)
        mask.paste(0, (x_off, y_off, x_off + new_w, y_off + new_h))

        def _to_bytes(img: Image.Image) -> bytes:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        response = self._get_vertex_client().models.edit_image(
            model="imagen-3.0-capability-001",
            prompt=prompt,
            reference_images=[
                types.RawReferenceImage(
                    reference_image=types.Image(
                        image_bytes=_to_bytes(canvas), mime_type="image/png",
                    ),
                    reference_id=0,
                ),
                types.MaskReferenceImage(
                    reference_image=types.Image(
                        image_bytes=_to_bytes(mask), mime_type="image/png",
                    ),
                    reference_id=0,
                    config=types.MaskReferenceConfig(
                        mask_mode="MASK_MODE_USER_PROVIDED",
                    ),
                ),
            ],
            config=types.EditImageConfig(
                edit_mode="EDIT_MODE_INPAINT_INSERTION",
                number_of_images=1,
            ),
        )

        if response.generated_images:
            result = response.generated_images[0].image.image_bytes
            logger.debug("gemini: edited %d bytes", len(result))
            return result

        raise RuntimeError("No image found in Gemini edit response")
