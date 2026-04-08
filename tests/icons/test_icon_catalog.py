"""Tests for the local icon catalog cache."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yoto_lib.icons.icon_catalog import (
    CATALOG_FILENAME,
    CATALOG_TTL_SECONDS,
    _filter_catalog,
    load_catalog,
    save_catalog,
    refresh_catalog,
    is_catalog_stale,
)


@pytest.fixture()
def cache_dir(tmp_path):
    return tmp_path / "icons"


class TestSaveCatalog:
    def test_writes_json_file(self, cache_dir):
        """save_catalog writes catalog.json with fetched_at and icons."""
        icons = [{"mediaId": "abc", "title": "Star"}]
        save_catalog(icons, cache_dir)

        catalog_path = cache_dir / CATALOG_FILENAME
        assert catalog_path.exists()

        data = json.loads(catalog_path.read_text())
        assert "fetched_at" in data
        assert data["icons"] == icons


class TestLoadCatalog:
    def test_returns_icons_from_cache(self, cache_dir):
        """load_catalog reads icons from a previously saved catalog."""
        icons = [{"mediaId": "abc", "title": "Star"}]
        save_catalog(icons, cache_dir)

        result = load_catalog(cache_dir)
        assert result == icons

    def test_returns_none_when_missing(self, cache_dir):
        """load_catalog returns None if no catalog file exists."""
        result = load_catalog(cache_dir)
        assert result is None


class TestIsCatalogStale:
    def test_missing_file_is_stale(self, cache_dir):
        """A nonexistent catalog is considered stale."""
        assert is_catalog_stale(cache_dir) is True

    def test_fresh_catalog_is_not_stale(self, cache_dir):
        """A catalog saved just now is not stale."""
        save_catalog([{"mediaId": "x", "title": "Y"}], cache_dir)
        assert is_catalog_stale(cache_dir) is False

    def test_old_catalog_is_stale(self, cache_dir):
        """A catalog with a fetched_at older than TTL is stale."""
        save_catalog([{"mediaId": "x", "title": "Y"}], cache_dir)
        catalog_path = cache_dir / CATALOG_FILENAME
        data = json.loads(catalog_path.read_text())
        data["fetched_at"] = time.time() - CATALOG_TTL_SECONDS - 1
        catalog_path.write_text(json.dumps(data))
        assert is_catalog_stale(cache_dir) is True


class TestFilterCatalog:
    def test_removes_empty_titles(self):
        icons = [{"mediaId": "a", "title": ""}, {"mediaId": "b", "title": "Star"}]
        assert len(_filter_catalog(icons)) == 1

    def test_removes_test_icons(self):
        icons = [
            {"mediaId": "a", "title": "01_MYO_radio_icon_test"},
            {"mediaId": "b", "title": "Star"},
        ]
        assert [i["title"] for i in _filter_catalog(icons)] == ["Star"]

    def test_deduplicates_by_title(self):
        icons = [
            {"mediaId": "a", "title": "Star"},
            {"mediaId": "b", "title": "star"},
            {"mediaId": "c", "title": "Moon"},
        ]
        result = _filter_catalog(icons)
        assert len(result) == 2
        assert result[0]["mediaId"] == "a"  # keeps first


class TestRefreshCatalog:
    def test_fetches_and_saves(self, cache_dir):
        """refresh_catalog fetches from API, saves to cache, returns icons."""
        api = MagicMock()
        api.get_public_icons.return_value = [
            {"mediaId": "abc", "title": "Star"},
            {"mediaId": "def", "title": "Moon"},
        ]

        result = refresh_catalog(api, cache_dir)

        assert len(result) == 2
        assert result[0]["title"] == "Star"
        api.get_public_icons.assert_called_once()

        saved = load_catalog(cache_dir)
        assert saved == result

    def test_downloads_missing_pngs(self, cache_dir):
        """refresh_catalog downloads icon PNGs that are not yet cached."""
        api = MagicMock()
        api.get_public_icons.return_value = [
            {"mediaId": "abc", "title": "Star"},
        ]
        fake_png = b"\x89PNG_fake_data"

        with patch(
            "yoto_lib.icons.download_icon", return_value=fake_png,
        ) as mock_dl:
            refresh_catalog(api, cache_dir)
            mock_dl.assert_called_once_with("abc", cache_dir)

    def test_skips_download_for_cached_pngs(self, cache_dir):
        """refresh_catalog skips download if the PNG already exists."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "abc.png").write_bytes(b"existing")

        api = MagicMock()
        api.get_public_icons.return_value = [
            {"mediaId": "abc", "title": "Star"},
        ]

        with patch(
            "yoto_lib.icons.download_icon",
        ) as mock_dl:
            refresh_catalog(api, cache_dir)
            mock_dl.assert_not_called()
