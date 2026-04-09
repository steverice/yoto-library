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
    message: str | None = None  # e.g. "Claude: Minor Service Outage"
    url: str | None = None  # e.g. "status.claude.com"


# ── Provider ABC ─────────────────────────────────────────────────────────────


class Provider(ABC):
    """Base class for all external service providers."""

    display_name: str  # Human-readable name, e.g. "Together AI"

    @classmethod
    def check_status(cls) -> ProviderStatus | None:
        """Check provider health.

        Returns ProviderStatus if the provider can report its health,
        or None if it has no way to check. Subclasses override this
        to check status pages, CLI availability, API key validity, etc.
        """
        return None

    @property
    def is_subscription(self) -> bool:
        """Whether this provider uses subscription billing (free with plan).

        Returns False by default (pay-per-call). Subclasses override
        when billing depends on configuration (e.g. Claude CLI vs SDK).
        """
        return False


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


# ── BetterStackMixin ────────────────────────────────────────────────────────


class BetterStackMixin:
    """Mixin for providers backed by a Better Stack status page.

    Subclasses set ``status_page_url`` to the status page root
    (e.g. ``https://status.together.ai``). The JSON endpoint at
    ``/index.json`` is queried for aggregate state.
    """

    status_page_url: str

    @classmethod
    def check_status(cls) -> ProviderStatus:
        return cls._fetch_betterstack(cls.status_page_url)

    @staticmethod
    def _fetch_betterstack(url: str) -> ProviderStatus:
        """Fetch and cache Better Stack status. Thread-safe, 5-min TTL."""
        json_url = url.rstrip("/") + "/index.json"
        host = urlparse(url).hostname

        now = time.monotonic()
        with _lock:
            if json_url in _cache:
                cached, ts = _cache[json_url]
                if now - ts < _TTL:
                    return cached

        try:
            resp = httpx.get(json_url, timeout=3, follow_redirects=True)
            resp.raise_for_status()
            state = resp.json()["data"]["attributes"]["aggregate_state"]
            healthy = state == "operational"
            name = resp.json()["data"]["attributes"].get("company_name", "Service")
            result = ProviderStatus(
                healthy=healthy,
                message=None if healthy else f"{name}: {state}",
                url=host,
            )
        except Exception:
            result = ProviderStatus(healthy=True, url=host)

        with _lock:
            _cache[json_url] = (result, now)
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
            if status is None:
                continue
            if not status.healthy:
                msg = status.message or "service issues detected"
                if status.url:
                    msg += f" ({status.url})"
                logger.warning("%s", msg)
        except Exception:  # noqa: S110
            pass  # health check must never make things worse
