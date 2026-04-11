"""Config-driven web scraping lyrics provider via Node.js/jsdom."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

_LYRICS_DIR = Path.home() / ".yoto" / "lyrics"
_SCRAPE_RUNNER = Path(__file__).parent / "scrape_runner.js"
_MIN_SIMILARITY = 0.6
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"  # noqa: E501

# Module-level session cache keyed by source URL
_index_cache: dict[str, dict[str, str]] = {}


@dataclass
class LyricsSource:
    name: str
    url: str
    index_js: str
    lyrics_js: str


def _normalize(s: str) -> str:
    """Lowercase and strip punctuation for fuzzy comparison."""
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _fetch_html(url: str) -> str | None:
    """Fetch HTML using Python httpx.

    Some sites use TLS fingerprinting to block Node.js fetch() and curl while
    allowing Python HTTP clients.  Pre-fetching in Python and passing the HTML
    to the JS runner via --html avoids these 403 blocks.
    """
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        logger.warning("_fetch_html: failed to fetch %s: %s", url, exc)
        return None


def load_lyrics_sources() -> list[LyricsSource]:
    """Scan _LYRICS_DIR/*.json and return a list of LyricsSource objects.

    Returns empty list if directory doesn't exist or no valid configs found.
    Sorted by filename for deterministic ordering.
    """
    if not _LYRICS_DIR.exists():
        return []

    sources: list[LyricsSource] = []
    for json_path in sorted(_LYRICS_DIR.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("lyrics_scrape: skipping malformed file %s: %s", json_path.name, exc)
            continue

        try:
            source = LyricsSource(
                name=data["name"],
                url=data["url"],
                index_js=data["index_js"],
                lyrics_js=data["lyrics_js"],
            )
        except KeyError as exc:
            logger.warning("lyrics_scrape: skipping %s — missing field %s", json_path.name, exc)
            continue

        sources.append(source)

    return sources


def _check_node() -> bool:
    """Return True if `node` is available on PATH."""
    return shutil.which("node") is not None


def _run_js(
    js_snippet: str,
    *,
    url: str | None = None,
    html_path: Path | None = None,
) -> Any:
    """Run a JS snippet via scrape_runner.js and return the parsed JSON result.

    Returns None on any error (timeout, non-zero exit, JSON parse failure, OSError).
    """
    cmd = ["node", str(_SCRAPE_RUNNER), "--js", js_snippet]
    if url is not None:
        cmd += ["--url", url]
    if html_path is not None:
        cmd += ["--html", str(html_path)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        logger.warning("lyrics_scrape: JS runner timed out")
        return None
    except OSError as exc:
        logger.warning("lyrics_scrape: failed to run JS runner: %s", exc)
        return None

    if result.returncode != 0:
        logger.warning("lyrics_scrape: JS runner exited with %d: %s", result.returncode, result.stderr.strip())
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("lyrics_scrape: failed to parse JS runner output as JSON: %s", exc)
        return None


def _fetch_index(source: LyricsSource) -> dict[str, str]:
    """Fetch and cache the title→URL index for a lyrics source.

    Returns a dict mapping normalised title → lyrics URL, or {} on failure.
    """
    if source.url in _index_cache:
        return _index_cache[source.url]

    html = _fetch_html(source.url)
    if html is None:
        _index_cache[source.url] = {}
        return {}

    html_path = Path(tempfile.mkstemp(suffix=".html")[1])
    html_path.write_text(html, encoding="utf-8")
    try:
        raw = _run_js(source.index_js, url=source.url, html_path=html_path)
    finally:
        html_path.unlink(missing_ok=True)

    if not isinstance(raw, list):
        logger.warning("lyrics_scrape: index for %s returned unexpected type %s", source.name, type(raw).__name__)
        # Cache the empty dict intentionally: transient failures are cached for
        # the session to avoid hammering a failing source repeatedly.
        _index_cache[source.url] = {}
        return {}

    index: dict[str, str] = {}
    for item in raw:
        try:
            index[_normalize(item["title"])] = item["url"]
        except (KeyError, TypeError) as exc:
            logger.warning("lyrics_scrape: skipping malformed index item from %s: %s", source.name, exc)

    _index_cache[source.url] = index
    return index


def _match_title(title: str, index: dict[str, str]) -> str | None:
    """Find the best-matching URL for a title in the index.

    Returns the URL of the best match if similarity >= _MIN_SIMILARITY, else None.
    """
    norm_title = _normalize(title)
    best_ratio = 0.0
    best_url: str | None = None

    for key, url in index.items():
        ratio = SequenceMatcher(None, norm_title, key).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_url = url

    if best_ratio >= _MIN_SIMILARITY:
        return best_url
    return None


def _fetch_lyrics(url: str, source: LyricsSource) -> str | None:
    """Fetch lyrics text from a specific URL using the source's lyrics_js snippet.

    Returns the stripped lyrics string, or None on failure.
    """
    html = _fetch_html(url)
    if html is None:
        return None

    html_path = Path(tempfile.mkstemp(suffix=".html")[1])
    html_path.write_text(html, encoding="utf-8")
    try:
        result = _run_js(source.lyrics_js, url=url, html_path=html_path)
    finally:
        html_path.unlink(missing_ok=True)

    if isinstance(result, str) and result.strip():
        return result.strip()
    logger.warning("_fetch_lyrics: expected str from JS snippet, got %s", type(result).__name__)
    return None


def fetch_lyrics_scrape(artist: str, title: str) -> tuple[str | None, str | None]:
    """Fetch lyrics using config-driven web scraping via jsdom.

    Tries each source in order; returns (lyrics_text, source_name) on first
    success, or (None, None) if nothing matched.
    """
    sources = load_lyrics_sources()
    if not sources:
        return (None, None)

    if not _check_node():
        logger.warning("lyrics_scrape: node not found on PATH — skipping scrape providers")
        return (None, None)

    for source in sources:
        index = _fetch_index(source)
        if not index:
            continue

        matched_url = _match_title(title, index)
        if matched_url is None:
            continue

        lyrics = _fetch_lyrics(matched_url, source)
        if lyrics is None:
            continue

        return (lyrics, source.name)

    return (None, None)
