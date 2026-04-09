"""Tests for provider status infrastructure: StatusPageMixin and @check_status_on_error."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from yoto_lib.providers.base import (
    _TTL,
    Provider,
    ProviderStatus,
    StatusPageMixin,
    _cache,
    _lock,
    check_status_on_error,
)

_STATUS_URL = "https://status.example.com/api/v2/status.json"


def _mock_response(indicator: str, description: str, name: str = "Claude") -> MagicMock:
    """Build a fake httpx.Response-like object."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "page": {"name": name},
        "status": {"indicator": indicator, "description": description},
    }
    return mock


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure _cache is empty before each test."""
    with _lock:
        _cache.clear()
    yield
    with _lock:
        _cache.clear()


# ── StatusPageMixin ───────────────────────────────────────────────────────────


class TestFetchStatusPage:
    def test_healthy_status_returns_healthy(self):
        resp = _mock_response("none", "All Systems Operational")
        with patch("httpx.get", return_value=resp):
            result = StatusPageMixin._fetch_statuspage(_STATUS_URL)
        assert result.healthy is True
        assert result.message is None

    def test_degraded_status_returns_unhealthy(self):
        resp = _mock_response("minor", "Minor Service Outage")
        with patch("httpx.get", return_value=resp):
            result = StatusPageMixin._fetch_statuspage("https://status.claude.com/api/v2/status.json")
        assert result.healthy is False
        assert result.message == "Claude: Minor Service Outage"
        assert result.url == "status.claude.com"

    def test_major_status_returns_unhealthy(self):
        resp = _mock_response("major", "Service Disruption")
        with patch("httpx.get", return_value=resp):
            result = StatusPageMixin._fetch_statuspage(_STATUS_URL)
        assert result.healthy is False

    def test_unreachable_returns_healthy(self):
        """A network error is treated as healthy (graceful degradation)."""
        with patch("httpx.get", side_effect=httpx.ConnectError("timeout")):
            result = StatusPageMixin._fetch_statuspage(_STATUS_URL)
        assert result.healthy is True

    def test_cache_hit(self):
        """Calling twice within the TTL makes only one HTTP request."""
        resp = _mock_response("none", "All Systems Operational")
        with patch("httpx.get", return_value=resp) as mock_get:
            StatusPageMixin._fetch_statuspage(_STATUS_URL)
            StatusPageMixin._fetch_statuspage(_STATUS_URL)
        assert mock_get.call_count == 1

    def test_cache_expired(self):
        """Calling twice after the TTL has elapsed makes two HTTP requests."""
        resp = _mock_response("none", "All Systems Operational")
        # _fetch_statuspage calls time.monotonic() once per invocation (at the top).
        # First call → now=0.0  (cache miss, stores ts=0.0)
        # Second call → now=TTL+1  (0.0 stored, TTL+1 - 0.0 >= TTL → miss, re-fetches)
        monotonic_values = [0.0, _TTL + 1.0]
        with (
            patch("yoto_lib.providers.base.time.monotonic", side_effect=monotonic_values),
            patch("httpx.get", return_value=resp) as mock_get,
        ):
            StatusPageMixin._fetch_statuspage(_STATUS_URL)
            StatusPageMixin._fetch_statuspage(_STATUS_URL)
        assert mock_get.call_count == 2


# ── @check_status_on_error ────────────────────────────────────────────────────


class FakeProvider(Provider):
    _status = ProviderStatus(healthy=True)

    @classmethod
    def check_status(cls) -> ProviderStatus:
        return cls._status


class TestCheckStatusOnError:
    def test_decorator_passes_through_on_success(self, caplog):
        @check_status_on_error(FakeProvider)
        def do_work():
            return "ok"

        with caplog.at_level(logging.WARNING):
            result = do_work()

        assert result == "ok"
        assert caplog.records == []

    def test_decorator_warns_on_exception_when_unhealthy(self, caplog):
        FakeProvider._status = ProviderStatus(
            healthy=False, message="Claude: Minor Service Outage", url="status.claude.com"
        )

        @check_status_on_error(FakeProvider)
        def do_work():
            raise RuntimeError("network failure")

        with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
            do_work()

        assert any("Minor Service Outage" in r.message for r in caplog.records)

        FakeProvider._status = ProviderStatus(healthy=True)

    def test_decorator_warns_on_none_when_unhealthy(self, caplog):
        FakeProvider._status = ProviderStatus(healthy=False, message="Claude: Partial Outage", url="status.claude.com")

        @check_status_on_error(FakeProvider)
        def do_work():
            return None

        with caplog.at_level(logging.WARNING):
            result = do_work()

        assert result is None
        assert any("Partial Outage" in r.message for r in caplog.records)

        FakeProvider._status = ProviderStatus(healthy=True)

    def test_decorator_silent_on_none_when_healthy(self, caplog):
        FakeProvider._status = ProviderStatus(healthy=True)

        @check_status_on_error(FakeProvider)
        def do_work():
            return None

        with caplog.at_level(logging.WARNING):
            result = do_work()

        assert result is None
        assert caplog.records == []

    def test_decorator_silent_on_exception_when_healthy(self, caplog):
        FakeProvider._status = ProviderStatus(healthy=True)

        @check_status_on_error(FakeProvider)
        def do_work():
            raise ValueError("unexpected error")

        with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="unexpected error"):
            do_work()

        assert caplog.records == []

    def test_decorator_multiple_providers_only_unhealthy_warns(self, caplog):
        class HealthyProvider(Provider):
            @classmethod
            def check_status(cls) -> ProviderStatus:
                return ProviderStatus(healthy=True)

        class SickProvider(Provider):
            @classmethod
            def check_status(cls) -> ProviderStatus:
                return ProviderStatus(healthy=False, message="SickProvider: Outage", url="status.sick.com")

        @check_status_on_error(HealthyProvider, SickProvider)
        def do_work():
            return None

        with caplog.at_level(logging.WARNING):
            do_work()

        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "SickProvider: Outage" in warnings[0]
