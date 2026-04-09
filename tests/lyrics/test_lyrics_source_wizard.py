"""Tests for the Claude-powered lyrics source setup wizard."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# _analyze_index_page
# ---------------------------------------------------------------------------


def _make_claude_mock(return_value: str | None):
    """Return a patched ClaudeProvider class whose instance's .call() returns return_value."""
    mock_instance = MagicMock()
    mock_instance.call.return_value = return_value
    mock_class = MagicMock(return_value=mock_instance)
    return mock_class


def test_analyze_index_page_success(tmp_path):
    """Claude returns valid JSON → returns {"name": ..., "index_js": ...}."""
    from yoto_lib.lyrics.lyrics_source_wizard import _analyze_index_page

    html_file = tmp_path / "index.html"
    html_file.write_text("<html><body></body></html>", encoding="utf-8")

    payload = '{"name": "Test Site", "index_js": "[]"}'
    mock_class = _make_claude_mock(payload)

    with patch("yoto_lib.lyrics.lyrics_source_wizard.ClaudeProvider", mock_class):
        result = _analyze_index_page(html_file)

    assert result == {"name": "Test Site", "index_js": "[]"}


def test_analyze_index_page_claude_failure(tmp_path):
    """Claude returns None → raises ValueError."""
    from yoto_lib.lyrics.lyrics_source_wizard import _analyze_index_page

    html_file = tmp_path / "index.html"
    html_file.write_text("<html></html>", encoding="utf-8")

    mock_class = _make_claude_mock(None)

    with patch("yoto_lib.lyrics.lyrics_source_wizard.ClaudeProvider", mock_class):
        with pytest.raises(ValueError, match="no response"):
            _analyze_index_page(html_file)


def test_analyze_index_page_invalid_json(tmp_path):
    """Claude returns non-JSON string → raises ValueError."""
    from yoto_lib.lyrics.lyrics_source_wizard import _analyze_index_page

    html_file = tmp_path / "index.html"
    html_file.write_text("<html></html>", encoding="utf-8")

    mock_class = _make_claude_mock("not json")

    with patch("yoto_lib.lyrics.lyrics_source_wizard.ClaudeProvider", mock_class):
        with pytest.raises(ValueError, match="not valid JSON"):
            _analyze_index_page(html_file)


# ---------------------------------------------------------------------------
# _analyze_lyrics_page
# ---------------------------------------------------------------------------


def test_analyze_lyrics_page_success(tmp_path):
    """Claude returns valid JSON with lyrics_js → returns dict."""
    from yoto_lib.lyrics.lyrics_source_wizard import _analyze_lyrics_page

    html_file = tmp_path / "song.html"
    html_file.write_text("<html><body>lyrics</body></html>", encoding="utf-8")

    payload = '{"lyrics_js": "document.body.textContent"}'
    mock_class = _make_claude_mock(payload)

    with patch("yoto_lib.lyrics.lyrics_source_wizard.ClaudeProvider", mock_class):
        result = _analyze_lyrics_page(html_file)

    assert result == {"lyrics_js": "document.body.textContent"}


def test_analyze_lyrics_page_missing_key(tmp_path):
    """Claude returns JSON without lyrics_js key → raises ValueError."""
    from yoto_lib.lyrics.lyrics_source_wizard import _analyze_lyrics_page

    html_file = tmp_path / "song.html"
    html_file.write_text("<html></html>", encoding="utf-8")

    payload = '{"wrong_key": "x"}'
    mock_class = _make_claude_mock(payload)

    with patch("yoto_lib.lyrics.lyrics_source_wizard.ClaudeProvider", mock_class):
        with pytest.raises(ValueError, match="lyrics_js"):
            _analyze_lyrics_page(html_file)


# ---------------------------------------------------------------------------
# run_wizard
# ---------------------------------------------------------------------------


def _make_httpx_response(text: str, status_code: int = 200) -> MagicMock:
    """Build a fake httpx.Response-like object."""
    mock = MagicMock()
    mock.text = text
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    return mock


def test_run_wizard_success():
    """Full happy-path: all mocks cooperate → returns dict with all expected keys."""
    from yoto_lib.lyrics.lyrics_source_wizard import run_wizard

    index_url = "https://example.com/songs"
    song_url = "https://example.com/song/one"
    song_title = "Song One"
    lyrics_text = "Some lyrics text"

    index_response = _make_httpx_response("<html>index</html>")
    song_response = _make_httpx_response("<html>song</html>")

    call_count = 0

    def fake_httpx_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return index_response
        return song_response

    run_js_count = 0

    def fake_run_js(js_snippet, *, url=None, html_path=None):
        nonlocal run_js_count
        run_js_count += 1
        if run_js_count == 1:
            # index validation
            return [{"title": song_title, "url": song_url}]
        # lyrics validation
        return lyrics_text

    index_analysis = {"name": "Test Archive", "index_js": "return []"}
    lyrics_analysis = {"lyrics_js": "return ''"}

    with (
        patch("yoto_lib.lyrics.lyrics_source_wizard.httpx.get", side_effect=fake_httpx_get),
        patch("yoto_lib.lyrics.lyrics_source_wizard._analyze_index_page", return_value=index_analysis),
        patch("yoto_lib.lyrics.lyrics_source_wizard._analyze_lyrics_page", return_value=lyrics_analysis),
        patch("yoto_lib.lyrics.lyrics_source_wizard._run_js", side_effect=fake_run_js),
    ):
        result = run_wizard(index_url)

    assert result["name"] == "Test Archive"
    assert result["url"] == index_url
    assert result["index_js"] == "return []"
    assert result["lyrics_js"] == "return ''"
    assert result["_sample_song"] == song_title
    assert result["_sample_lyrics"] == lyrics_text


def test_run_wizard_fetch_error():
    """httpx.get raises HTTPError → raises ValueError."""
    from yoto_lib.lyrics.lyrics_source_wizard import run_wizard

    with (
        patch(
            "yoto_lib.lyrics.lyrics_source_wizard.httpx.get",
            side_effect=httpx.HTTPError("connection failed"),
        ),
        pytest.raises(ValueError, match="Failed to fetch"),
    ):
        run_wizard("https://example.com/songs")


def test_run_wizard_index_js_no_results():
    """_run_js returns [] for index step → raises ValueError."""
    from yoto_lib.lyrics.lyrics_source_wizard import run_wizard

    index_response = _make_httpx_response("<html>index</html>")

    index_analysis = {"name": "Test Archive", "index_js": "return []"}

    with (
        patch("yoto_lib.lyrics.lyrics_source_wizard.httpx.get", return_value=index_response),
        patch("yoto_lib.lyrics.lyrics_source_wizard._analyze_index_page", return_value=index_analysis),
        patch("yoto_lib.lyrics.lyrics_source_wizard._run_js", return_value=[]),
    ):
        with pytest.raises(ValueError, match="no results"):
            run_wizard("https://example.com/songs")


def test_run_wizard_lyrics_js_no_content():
    """_run_js returns None on lyrics step → raises ValueError."""
    from yoto_lib.lyrics.lyrics_source_wizard import run_wizard

    index_url = "https://example.com/songs"
    song_url = "https://example.com/song/one"
    song_title = "Song One"

    index_response = _make_httpx_response("<html>index</html>")
    song_response = _make_httpx_response("<html>song</html>")

    call_count = 0

    def fake_httpx_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return index_response
        return song_response

    run_js_results = [
        [{"title": song_title, "url": song_url}],  # index validation succeeds
        None,  # lyrics validation returns nothing
    ]

    index_analysis = {"name": "Test Archive", "index_js": "return []"}
    lyrics_analysis = {"lyrics_js": "return ''"}

    with (
        patch("yoto_lib.lyrics.lyrics_source_wizard.httpx.get", side_effect=fake_httpx_get),
        patch("yoto_lib.lyrics.lyrics_source_wizard._analyze_index_page", return_value=index_analysis),
        patch("yoto_lib.lyrics.lyrics_source_wizard._analyze_lyrics_page", return_value=lyrics_analysis),
        patch("yoto_lib.lyrics.lyrics_source_wizard._run_js", side_effect=run_js_results),
    ):
        with pytest.raises(ValueError, match="no content"):
            run_wizard(index_url)
