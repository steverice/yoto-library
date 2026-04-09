"""Tests for provider base classes — StatusPageMixin, check_status_on_error."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yoto_lib.providers.base import (
    Provider,
    ProviderStatus,
    StatusPageMixin,
    _cache,
    _warn_unhealthy,
    check_status_on_error,
)


class FakeProvider(StatusPageMixin, Provider):
    status_page_url = "https://status.example.com/api/v2/status.json"


class TestProviderStatus:
    def test_healthy_status(self):
        status = ProviderStatus(healthy=True)
        assert status.healthy is True
        assert status.message is None
        assert status.url is None

    def test_unhealthy_status_with_details(self):
        status = ProviderStatus(
            healthy=False,
            message="Service degraded",
            url="status.example.com",
        )
        assert status.healthy is False
        assert status.message == "Service degraded"
        assert status.url == "status.example.com"


class TestStatusPageMixin:
    def setup_method(self):
        # Clear the cache between tests
        _cache.clear()

    def test_healthy_status_page(self):
        """Returns healthy when status page reports indicator=none."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": {"indicator": "none", "description": "All Systems Operational"},
            "page": {"name": "Example"},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("yoto_lib.providers.base.httpx.get", return_value=mock_resp):
            status = FakeProvider.check_status()

        assert status.healthy is True

    def test_unhealthy_status_page(self):
        """Returns unhealthy when status page reports issues."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": {"indicator": "major", "description": "Major System Outage"},
            "page": {"name": "Example Service"},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("yoto_lib.providers.base.httpx.get", return_value=mock_resp):
            status = FakeProvider.check_status()

        assert status.healthy is False
        assert "Example Service" in status.message
        assert "Major System Outage" in status.message

    def test_network_error_defaults_to_healthy(self):
        """Returns healthy when status page is unreachable."""
        with patch("yoto_lib.providers.base.httpx.get", side_effect=OSError("timeout")):
            status = FakeProvider.check_status()

        assert status.healthy is True

    def test_caches_result(self):
        """Second call within TTL returns cached result."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": {"indicator": "none"},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("yoto_lib.providers.base.httpx.get", return_value=mock_resp) as mock_get:
            FakeProvider.check_status()
            FakeProvider.check_status()

        # Only one actual HTTP call
        assert mock_get.call_count == 1


class TestCheckStatusOnError:
    def setup_method(self):
        _cache.clear()

    def test_normal_return_no_check(self):
        """When function returns normally, no status check needed."""

        @check_status_on_error(FakeProvider)
        def good_func():
            return "ok"

        with patch.object(FakeProvider, "check_status") as mock_check:
            result = good_func()

        assert result == "ok"
        mock_check.assert_not_called()

    def test_none_return_triggers_check(self):
        """When function returns None, providers are checked."""

        @check_status_on_error(FakeProvider)
        def none_func():
            return None

        with patch.object(FakeProvider, "check_status", return_value=ProviderStatus(healthy=True)):
            result = none_func()

        assert result is None

    def test_exception_triggers_check_and_reraises(self):
        """When function raises, providers are checked and exception re-raised."""

        @check_status_on_error(FakeProvider)
        def error_func():
            raise ValueError("broken")

        with (
            patch.object(FakeProvider, "check_status", return_value=ProviderStatus(healthy=True)),
            pytest.raises(ValueError, match="broken"),
        ):
            error_func()

    def test_warn_unhealthy_logs_warning(self, caplog):
        """_warn_unhealthy logs a warning for unhealthy providers."""
        status = ProviderStatus(
            healthy=False,
            message="Service Down",
            url="status.example.com",
        )
        with patch.object(FakeProvider, "check_status", return_value=status):
            import logging

            with caplog.at_level(logging.WARNING):
                _warn_unhealthy((FakeProvider,))

        assert any("Service Down" in r.message for r in caplog.records)

    def test_warn_unhealthy_swallows_check_errors(self):
        """Health check failures do not propagate."""
        with patch.object(FakeProvider, "check_status", side_effect=RuntimeError("boom")):
            # Should not raise
            _warn_unhealthy((FakeProvider,))
