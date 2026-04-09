"""Provider base classes and health-check infrastructure."""

from __future__ import annotations

import functools
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# ── ProviderStatus ───────────────────────────────────────────────────────────


@dataclass
class ProviderStatus:
    """Result of a provider health check."""
    healthy: bool
    message: str | None = None   # e.g. "Claude: Minor Service Outage"
    url: str | None = None       # e.g. "status.claude.com"


# ── Provider ABC ─────────────────────────────────────────────────────────────


class Provider(ABC):
    """Base class for all external service providers."""

    @classmethod
    @abstractmethod
    def check_status(cls) -> ProviderStatus:
        """Check provider health.

        Implementations may check status pages, CLI availability,
        API key validity, credit balance, etc.
        """
        ...


class ImageProvider(Provider):
    """Base class for image generation providers."""

    @abstractmethod
    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Generate an image from a text prompt. Returns PNG bytes."""
        ...


# ── StatusPageMixin ──────────────────────────────────────────────────────────


_cache: dict[str, tuple[ProviderStatus, float]] = {}
_lock = threading.Lock()
_TTL = 300  # 5 minutes


class StatusPageMixin:
    """Mixin for providers backed by a statuspage.io status page.

    Subclasses set ``status_page_url`` to the ``/api/v2/status.json`` endpoint.
    """
    status_page_url: str

    @classmethod
    def check_status(cls) -> ProviderStatus:
        return cls._fetch_statuspage(cls.status_page_url)

    @staticmethod
    def _fetch_statuspage(url: str) -> ProviderStatus:
        """Fetch and cache statuspage.io status. Thread-safe, 5-min TTL."""
        now = time.monotonic()
        with _lock:
            if url in _cache:
                cached, ts = _cache[url]
                if now - ts < _TTL:
                    return cached

        try:
            resp = httpx.get(url, timeout=3)
            resp.raise_for_status()
            data = resp.json()
            indicator = data["status"]["indicator"]
            if indicator == "none":
                result = ProviderStatus(healthy=True)
            else:
                name = data.get("page", {}).get("name", "Service")
                desc = data["status"].get("description", "issues detected")
                host = urlparse(url).hostname
                result = ProviderStatus(
                    healthy=False,
                    message=f"{name}: {desc}",
                    url=host,
                )
        except Exception:
            result = ProviderStatus(healthy=True)  # assume healthy if unreachable

        with _lock:
            _cache[url] = (result, now)
        return result


# ── @check_status_on_error ───────────────────────────────────────────────────


_F = TypeVar("_F", bound=Callable[..., Any])


def check_status_on_error(*provider_classes: type[Provider]) -> Callable[[_F], _F]:
    """Decorator for logic functions that use external providers.

    On error (exception or None return), checks each provider's health
    and logs a warning if any are unhealthy.

    Usage::

        @check_status_on_error(OpenAIProvider)
        def generate_cover_if_missing(playlist, ...): ...

        @check_status_on_error(ClaudeProvider, RetroDiffusionProvider)
        def select_icons(tracks, ...): ...
    """
    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = func(*args, **kwargs)
                if result is None:
                    _warn_unhealthy(provider_classes)
                return result
            except Exception:
                _warn_unhealthy(provider_classes)
                raise
        return wrapper  # type: ignore[return-value]
    return decorator


def _warn_unhealthy(provider_classes: tuple[type[Provider], ...]) -> None:
    for cls in provider_classes:
        try:
            status = cls.check_status()
            if not status.healthy:
                msg = status.message or "service issues detected"
                if status.url:
                    msg += f" ({status.url})"
                logger.warning("%s", msg)
        except Exception:
            pass  # health check must never make things worse
