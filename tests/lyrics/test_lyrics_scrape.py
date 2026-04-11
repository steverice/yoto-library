"""Tests for lyrics_scrape module."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# load_lyrics_sources
# ---------------------------------------------------------------------------


def test_load_lyrics_sources_empty_dir(tmp_path):
    """Directory doesn't exist → returns empty list."""
    from yoto_lib.lyrics import lyrics_scrape

    nonexistent = tmp_path / "no_such_dir"
    with patch.object(lyrics_scrape, "_LYRICS_DIR", nonexistent):
        result = lyrics_scrape.load_lyrics_sources()

    assert result == []


def test_load_lyrics_sources_parses_config(tmp_path):
    """Valid JSON file → returns [LyricsSource(...)]."""
    from yoto_lib.lyrics import lyrics_scrape
    from yoto_lib.lyrics.lyrics_scrape import LyricsSource

    config = {
        "name": "MySite",
        "url": "https://example.com/songs",
        "index_js": "document.querySelectorAll('a')",
        "lyrics_js": "document.querySelector('.lyrics').textContent",
    }
    (tmp_path / "mysite.json").write_text(json.dumps(config), encoding="utf-8")

    with patch.object(lyrics_scrape, "_LYRICS_DIR", tmp_path):
        result = lyrics_scrape.load_lyrics_sources()

    assert len(result) == 1
    assert result[0] == LyricsSource(
        name="MySite",
        url="https://example.com/songs",
        index_js="document.querySelectorAll('a')",
        lyrics_js="document.querySelector('.lyrics').textContent",
    )


def test_load_lyrics_sources_skips_invalid_json(tmp_path):
    """Malformed JSON → returns [], no crash."""
    from yoto_lib.lyrics import lyrics_scrape

    (tmp_path / "bad.json").write_text("{not valid json", encoding="utf-8")

    with patch.object(lyrics_scrape, "_LYRICS_DIR", tmp_path):
        result = lyrics_scrape.load_lyrics_sources()

    assert result == []


def test_load_lyrics_sources_skips_missing_fields(tmp_path):
    """JSON missing lyrics_js → returns []."""
    from yoto_lib.lyrics import lyrics_scrape

    config = {
        "name": "Incomplete",
        "url": "https://example.com",
        "index_js": "[]",
        # lyrics_js deliberately omitted
    }
    (tmp_path / "incomplete.json").write_text(json.dumps(config), encoding="utf-8")

    with patch.object(lyrics_scrape, "_LYRICS_DIR", tmp_path):
        result = lyrics_scrape.load_lyrics_sources()

    assert result == []


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------


def test_normalize():
    """'It's a Beautiful Day!' → 'its a beautiful day'"""
    from yoto_lib.lyrics.lyrics_scrape import _normalize

    assert _normalize("It's a Beautiful Day!") == "its a beautiful day"


# ---------------------------------------------------------------------------
# _match_title
# ---------------------------------------------------------------------------


def test_match_title_exact():
    """Exact match returns URL."""
    from yoto_lib.lyrics.lyrics_scrape import _match_title

    index = {"accidents happen": "https://example.com/accidents-happen"}
    result = _match_title("accidents happen", index)
    assert result == "https://example.com/accidents-happen"


def test_match_title_fuzzy():
    """'accidents happen' vs 'accidents happen v1' → should match."""
    from yoto_lib.lyrics.lyrics_scrape import _match_title

    index = {"accidents happen v1": "https://example.com/accidents-happen-v1"}
    result = _match_title("accidents happen", index)
    assert result == "https://example.com/accidents-happen-v1"


def test_match_title_no_match():
    """Completely different title → returns None."""
    from yoto_lib.lyrics.lyrics_scrape import _match_title

    index = {"twinkle twinkle little star": "https://example.com/twinkle"}
    result = _match_title("Old MacDonald Had a Farm", index)
    assert result is None


# ---------------------------------------------------------------------------
# fetch_lyrics_scrape — integration-level (mocked internals)
# ---------------------------------------------------------------------------


def _make_source(name: str = "TestSite") -> object:
    from yoto_lib.lyrics.lyrics_scrape import LyricsSource

    return LyricsSource(
        name=name,
        url="https://example.com/songs",
        index_js="dummy_index_js",
        lyrics_js="dummy_lyrics_js",
    )


@patch.dict("yoto_lib.lyrics.lyrics_scrape._index_cache", {}, clear=True)
def test_fetch_lyrics_scrape_no_sources():
    """load_lyrics_sources returns [] → (None, None) without calling node."""
    from yoto_lib.lyrics.lyrics_scrape import fetch_lyrics_scrape

    with (
        patch("yoto_lib.lyrics.lyrics_scrape.load_lyrics_sources", return_value=[]),
        patch("yoto_lib.lyrics.lyrics_scrape._check_node") as mock_node,
    ):
        result = fetch_lyrics_scrape("Artist", "Title")

    assert result == (None, None)
    mock_node.assert_not_called()


@patch.dict("yoto_lib.lyrics.lyrics_scrape._index_cache", {}, clear=True)
def test_fetch_lyrics_scrape_no_node(caplog):
    """node not on PATH → (None, None) with warning logged."""
    import logging

    from yoto_lib.lyrics.lyrics_scrape import fetch_lyrics_scrape

    source = _make_source()
    with (
        patch("yoto_lib.lyrics.lyrics_scrape.load_lyrics_sources", return_value=[source]),
        patch("yoto_lib.lyrics.lyrics_scrape._check_node", return_value=False),
        caplog.at_level(logging.WARNING, logger="yoto_lib.lyrics_scrape"),
    ):
        result = fetch_lyrics_scrape("Artist", "Title")

    assert result == (None, None)
    assert any("node" in msg.lower() for msg in caplog.messages)


def test_fetch_lyrics_scrape_success():
    """Mock _run_js to return index + lyrics → (lyrics_text, source_name)."""
    from yoto_lib.lyrics.lyrics_scrape import fetch_lyrics_scrape

    source = _make_source("TestSite")
    index_data = [{"title": "My Song", "url": "https://example.com/my-song"}]
    lyrics_text = "These are the lyrics\nLine two"

    call_count = 0

    def fake_run_js(js_snippet, *, url=None, html_path=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: index
            return index_data
        # Second call: lyrics
        return lyrics_text

    with (
        patch("yoto_lib.lyrics.lyrics_scrape.load_lyrics_sources", return_value=[source]),
        patch("yoto_lib.lyrics.lyrics_scrape._check_node", return_value=True),
        patch("yoto_lib.lyrics.lyrics_scrape._fetch_html", return_value="<html></html>"),
        patch("yoto_lib.lyrics.lyrics_scrape._run_js", side_effect=fake_run_js),
        patch.dict("yoto_lib.lyrics.lyrics_scrape._index_cache", {}, clear=True),
    ):
        result = fetch_lyrics_scrape("Artist", "My Song")

    assert result == (lyrics_text, "TestSite")


def test_fetch_lyrics_scrape_no_match():
    """Index returns no matching title → (None, None)."""
    from yoto_lib.lyrics.lyrics_scrape import fetch_lyrics_scrape

    source = _make_source("TestSite")
    index_data = [{"title": "Completely Different Song", "url": "https://example.com/different"}]

    with (
        patch("yoto_lib.lyrics.lyrics_scrape.load_lyrics_sources", return_value=[source]),
        patch("yoto_lib.lyrics.lyrics_scrape._check_node", return_value=True),
        patch("yoto_lib.lyrics.lyrics_scrape._fetch_html", return_value="<html></html>"),
        patch("yoto_lib.lyrics.lyrics_scrape._run_js", return_value=index_data),
        patch.dict("yoto_lib.lyrics.lyrics_scrape._index_cache", {}, clear=True),
    ):
        result = fetch_lyrics_scrape("Artist", "Old MacDonald Had a Farm")

    assert result == (None, None)


# ---------------------------------------------------------------------------
# _run_js
# ---------------------------------------------------------------------------


def test_run_js_node_timeout(caplog):
    """subprocess raises TimeoutExpired → returns None, logs warning."""
    import logging

    from yoto_lib.lyrics.lyrics_scrape import _run_js

    with (
        patch(
            "yoto_lib.lyrics.lyrics_scrape.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["node"], timeout=30),
        ),
        caplog.at_level(logging.WARNING, logger="yoto_lib.lyrics_scrape"),
    ):
        result = _run_js("someSnippet()", url="https://example.com")

    assert result is None
    assert any("timed out" in msg.lower() for msg in caplog.messages)


def test_run_js_nonzero_exit(caplog):
    """subprocess returns returncode=1 → returns None, logs warning."""
    import logging

    from yoto_lib.lyrics.lyrics_scrape import _run_js

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "some error"

    with (
        patch("yoto_lib.lyrics.lyrics_scrape.subprocess.run", return_value=mock_result),
        caplog.at_level(logging.WARNING, logger="yoto_lib.lyrics_scrape"),
    ):
        result = _run_js("someSnippet()", url="https://example.com")

    assert result is None
    assert any("exited" in msg.lower() or "non-zero" in msg.lower() for msg in caplog.messages)
