"""OpenAI image provider implementation."""
from __future__ import annotations

import base64
import logging

from openai import OpenAI

from yoto_lib.providers.base import Provider, ProviderStatus, StatusPageMixin

logger = logging.getLogger(__name__)


# Supported sizes for GPT Image models
_SUPPORTED_SIZES = [
    (1024, 1024),
    (1024, 1536),
    (1536, 1024),
]


def _nearest_size(width: int, height: int) -> tuple[int, int]:
    """Map requested dimensions to the nearest supported size."""
    def _distance(size: tuple[int, int]) -> float:
        sw, sh = size
        return (sw - width) ** 2 + (sh - height) ** 2

    return min(_SUPPORTED_SIZES, key=_distance)


class OpenAIProvider(StatusPageMixin, Provider):
    status_page_url = "https://status.openai.com/api/v2/status.json"
    """Generates images using the OpenAI images API (gpt-image-1.5)."""

    def __init__(self) -> None:
        self._client = OpenAI()

    def generate(self, prompt: str, width: int, height: int, quality: str = "medium") -> bytes:
        """Generate an image from a text prompt. Returns PNG bytes."""
        nearest_w, nearest_h = _nearest_size(width, height)
        size_str = f"{nearest_w}x{nearest_h}"
        logger.debug("openai: generating %s quality=%s, prompt=%.80s...", size_str, quality, prompt)

        response = self._client.images.generate(
            model="gpt-image-1.5",
            prompt=prompt,
            size=size_str,
            quality=quality,
        )

        b64_data = response.data[0].b64_json
        result = base64.b64decode(b64_data)
        logger.debug("openai: generated %d bytes", len(result))
        from yoto_lib.costs import get_tracker
        get_tracker().record(f"openai_generate_{quality}")
        return result

    def edit(self, image_bytes: bytes, mask_bytes: bytes, prompt: str, width: int, height: int, quality: str = "medium") -> bytes:
        """Edit an image. Pass empty mask_bytes to let the model edit freely. Returns PNG bytes."""
        import io as _io
        nearest_w, nearest_h = _nearest_size(width, height)
        size_str = f"{nearest_w}x{nearest_h}"
        logger.debug("openai: editing %s quality=%s mask=%s prompt=%.80s...", size_str, quality, bool(mask_bytes), prompt)

        kwargs: dict = dict(
            model="gpt-image-1.5",
            image=("image.png", _io.BytesIO(image_bytes), "image/png"),
            prompt=prompt,
            size=size_str,
        )
        if mask_bytes:
            kwargs["mask"] = ("mask.png", _io.BytesIO(mask_bytes), "image/png")
        kwargs["quality"] = quality

        response = self._client.images.edit(**kwargs)

        b64_data = response.data[0].b64_json
        result = base64.b64decode(b64_data)
        logger.debug("openai: edited %d bytes", len(result))
        from yoto_lib.costs import get_tracker
        get_tracker().record(f"openai_edit_{quality}")
        return result

