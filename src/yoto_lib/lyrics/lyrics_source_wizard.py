"""Claude-powered wizard that generates lyrics source config from a URL."""

from __future__ import annotations

import json
import logging
import random
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from yoto_lib.providers.claude_provider import ClaudeProvider

from .lyrics_scrape import _run_js

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _analyze_index_page(html_path: Path) -> dict[str, str]:
    """Call Claude Sonnet to analyze an index page HTML and generate index_js.

    Returns {"name": str, "index_js": str}.
    Raises ValueError on failure.
    """
    prompt = (
        f"You are analyzing an HTML file at {html_path} that contains a song index page "
        f"(a list of songs with links to their lyrics pages).\n\n"
        f"Please read the file and write a JavaScript snippet that extracts an array of "
        f"{{title: string, url: string}} objects from the page DOM using "
        f"document.querySelectorAll or similar DOM APIs.\n\n"
        f"Also suggest a short human-readable name for this lyrics source "
        f"(e.g. 'Neighborhood Archive', 'Kids Song Hub').\n\n"
        f"Notes:\n"
        f"- The JS will run in jsdom, so standard DOM APIs apply.\n"
        f"- All <a> hrefs will be absolute due to the base URL setting.\n"
        f"- The snippet should evaluate to (or return) the array directly.\n\n"
        f"Respond with ONLY a JSON object, no explanation:\n"
        f'{{"name": "...", "index_js": "..."}}'
    )

    response = ClaudeProvider().call(prompt, model="sonnet", allowed_tools="Read", extract_json=True)

    if response is None:
        raise ValueError("Claude returned no response for index page analysis")

    try:
        data = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude response was not valid JSON: {exc}") from exc

    if "name" not in data or "index_js" not in data:
        missing = [k for k in ("name", "index_js") if k not in data]
        raise ValueError(f"Claude response missing required keys: {missing}")

    return {"name": str(data["name"]), "index_js": str(data["index_js"])}


def _analyze_lyrics_page(html_path: Path) -> dict[str, str]:
    """Call Claude Sonnet to analyze a lyrics page HTML and generate lyrics_js.

    Returns {"lyrics_js": str}.
    Raises ValueError on failure.
    """
    prompt = (
        f"You are analyzing an HTML file at {html_path} that contains a single song's "
        f"lyrics page.\n\n"
        f"Please read the file and write a JavaScript snippet that extracts just the "
        f"lyrics text as a plain string from the page DOM.\n\n"
        f"Important:\n"
        f"- The snippet should return (or evaluate to) a plain string, not an array.\n"
        f"- Exclude navigation, ads, copyright notices, and site chrome — only the song lyrics.\n"
        f"- The JS will run in jsdom with standard DOM APIs available.\n\n"
        f"Respond with ONLY a JSON object, no explanation:\n"
        f'{{"lyrics_js": "..."}}'
    )

    response = ClaudeProvider().call(prompt, model="sonnet", allowed_tools="Read", extract_json=True)

    if response is None:
        raise ValueError("Claude returned no response for lyrics page analysis")

    try:
        data = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude response was not valid JSON: {exc}") from exc

    if "lyrics_js" not in data:
        raise ValueError(f"Claude response missing required key 'lyrics_js'; got keys: {list(data.keys())}")

    return {"lyrics_js": str(data["lyrics_js"])}


def run_wizard(
    index_url: str,
    on_step: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Analyze a lyrics site and generate a scraping config. Returns config dict.

    Args:
        index_url: URL of the song index page.
        on_step: Optional callback called with a human-readable status string
            at the start of each step (for progress display).

    Raises ValueError if any step fails.
    """

    def _step(msg: str) -> None:
        logger.debug("lyrics_source_wizard: %s", msg)
        if on_step:
            on_step(msg)

    headers = {"User-Agent": _USER_AGENT}

    # Step 1: fetch index page
    _step("Fetching index page…")
    try:
        response = httpx.get(index_url, follow_redirects=True, timeout=30.0, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"Failed to fetch index URL {index_url!r}: {exc}") from exc

    index_html = response.text

    index_tmp: Path | None = None
    song_tmp: Path | None = None

    try:
        # Save index HTML to tempfile
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as f:
            f.write(index_html)
            index_tmp = Path(f.name)

        # Step 2: analyze index page
        _step("Analyzing index page with Claude…")
        index_result = _analyze_index_page(index_tmp)
        name = index_result["name"]
        index_js = index_result["index_js"]

        # Step 3: validate index_js
        _step("Validating index snippet…")
        songs = _run_js(index_js, url=index_url, html_path=index_tmp)
        if not songs or not isinstance(songs, list):
            raise ValueError(
                "index_js generated by Claude returned no results — the page structure may not be supported"
            )

        # Step 4: pick a random song
        song = random.choice(songs)
        song_url = song.get("url") if isinstance(song, dict) else None
        if not song_url:
            raise ValueError("index_js returned songs without 'url' fields")
        song_title = song.get("title", "Unknown") if isinstance(song, dict) else "Unknown"

        # Step 5: fetch song lyrics page
        _step(f"Fetching sample song: {song_title}…")
        try:
            song_response = httpx.get(song_url, follow_redirects=True, timeout=30.0, headers=headers)
            song_response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"Failed to fetch song URL {song_url!r}: {exc}") from exc

        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as f:
            f.write(song_response.text)
            song_tmp = Path(f.name)

        # Step 6: analyze lyrics page
        _step("Analyzing lyrics page with Claude…")
        lyrics_result = _analyze_lyrics_page(song_tmp)
        lyrics_js = lyrics_result["lyrics_js"]

        # Step 7: validate lyrics_js
        _step("Validating lyrics snippet…")
        lyrics_text = _run_js(lyrics_js, url=song_url, html_path=song_tmp)
        if not lyrics_text or not isinstance(lyrics_text, str) or not lyrics_text.strip():
            raise ValueError("lyrics_js generated by Claude returned no content")

        # Step 8: return config dict
        return {
            "name": name,
            "url": index_url,
            "index_js": index_js,
            "lyrics_js": lyrics_js,
            "_sample_song": song_title,
            "_sample_lyrics": lyrics_text,
        }

    finally:
        if index_tmp is not None:
            index_tmp.unlink(missing_ok=True)
        if song_tmp is not None:
            song_tmp.unlink(missing_ok=True)
