"""Yoto CLI — manage CYO playlists as folders on disk."""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import click
from click.shell_completion import CompletionItem
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

logger = logging.getLogger(__name__)

from yoto_lib.billing.costs import get_tracker
from yoto_lib.billing import persist_session
from yoto_lib.mka import read_tags


def _print_cost_summary():
    from yoto_cli.progress import _console
    tracker = get_tracker()
    if not tracker.has_records():
        return
    persist_session(tracker)
    for line in tracker.summary_lines():
        _console.print(f"[dim]{line}[/dim]")


# ── Logging setup ────────────────────────────────────────────────────────────

LOG_DIR = Path.home() / ".yoto"
LOG_FILE = LOG_DIR / "yoto.log"


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging: rotating file at DEBUG, console at WARNING (or DEBUG if verbose)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Configure both yoto_lib and yoto_cli loggers
    for name in ("yoto_lib", "yoto_cli"):
        log = logging.getLogger(name)
        if log.handlers:
            return  # already configured (e.g. tests calling cli multiple times)
        log.setLevel(logging.DEBUG)

        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        log.addHandler(file_handler)

        from rich.logging import RichHandler
        from yoto_cli.progress import _console as rich_console
        console_handler = RichHandler(
            console=rich_console,
            show_time=False,
            show_path=False,
            markup=False,
        )
        env_level = os.environ.get("YOTO_LOG_LEVEL", "").upper()
        if verbose:
            console_handler.setLevel(logging.DEBUG)
        elif env_level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            console_handler.setLevel(getattr(logging, env_level))
        else:
            console_handler.setLevel(logging.WARNING)
        log.addHandler(console_handler)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_card_id(value: str) -> bool:
    """
    Heuristic: treat as card_id if it is a short (<=10 chars) alphanumeric
    string that does NOT exist as a path on disk.
    """
    return (
        bool(re.fullmatch(r"[A-Za-z0-9]{1,10}", value))
        and not Path(value).exists()
    )


def _strip_track_number(stem: str) -> str:
    """Strip leading track number prefix from a filename stem.

    Handles: '01 Song', '01. Song', '01 - Song', '1-Song', '01_Song'
    """
    stripped = re.sub(r"^\d+[\s.\-_]+", "", stem)
    return stripped if stripped else stem


# ── Shell completion helpers ──────────────────────────────────────────────────


def _has_custom_icon(path: Path) -> bool:
    """Check if an MKA file has an icon attachment."""
    import json
    import subprocess

    try:
        result = subprocess.run(
            ["mkvmerge", "-J", str(path)],
            capture_output=True, text=True, timeout=5,
        )
        data = json.loads(result.stdout)
        return any(a.get("file_name") == "icon" for a in data.get("attachments", []))
    except Exception:
        return False


def _complete_path(incomplete: str, filter_fn):
    """Complete filesystem paths, yielding dirs (for navigation) and filtered files."""
    inc_path = Path(incomplete) if incomplete else Path(".")

    if inc_path.is_dir() and (not incomplete or incomplete.endswith("/")):
        search_dir = inc_path
        prefix = ""
    else:
        search_dir = inc_path.parent
        prefix = inc_path.name

    if not search_dir.is_dir():
        return []

    items = []
    for entry in sorted(search_dir.iterdir()):
        if entry.name.startswith("."):
            continue
        if prefix and not entry.name.lower().startswith(prefix.lower()):
            continue

        value = entry.name if str(search_dir) == "." else str(search_dir / entry.name)

        if entry.is_dir():
            items.append(CompletionItem(value + "/", type="plain"))
        elif filter_fn(entry):
            items.append(CompletionItem(value, type="plain"))
    return items


def _complete_weblocs(ctx, param, incomplete):
    """Complete .webloc file paths."""
    return _complete_path(incomplete, lambda p: p.suffix.lower() == ".webloc")


def _complete_dirs(ctx, param, incomplete):
    """Complete directory paths only."""
    return _complete_path(incomplete, lambda _: False)


def _complete_unimported_dirs(ctx, param, incomplete):
    """Complete directories that lack a playlist.jsonl, falling back to all dirs."""
    results = _complete_path(
        incomplete,
        lambda p: False,  # no files, dirs only
    )
    # Filter to dirs without playlist.jsonl
    filtered = [
        item for item in results
        if not item.value.endswith("/") or not (Path(item.value) / "playlist.jsonl").exists()
    ]
    return filtered if any(item.value.endswith("/") for item in filtered) else results


def _is_mka(p):
    return p.suffix.lower() == ".mka"


def _complete_mka_with_icon(ctx, param, incomplete):
    """Complete .mka files that have a custom icon, falling back to all .mka."""
    results = _complete_path(incomplete, lambda p: _is_mka(p) and _has_custom_icon(p))
    if not any(item.type == "plain" and not item.value.endswith("/") for item in results):
        results = _complete_path(incomplete, _is_mka)
    return results


def _has_lyrics(path: Path) -> bool:
    """Check if an MKA file has a LYRICS tag."""
    try:
        tags = read_tags(path)
        return bool(tags.get("lyrics"))
    except Exception:
        return False


def _complete_lyrics_path(ctx, param, incomplete):
    """Complete dirs and .mka files for lyrics command.

    With --show: prefer tracks that have lyrics.
    Without --show: prefer tracks that lack lyrics.
    Falls back to all .mka files if the preferred filter matches none.
    """
    show_mode = ctx.params.get("show", False)
    if show_mode:
        results = _complete_path(incomplete, lambda p: _is_mka(p) and _has_lyrics(p))
    else:
        results = _complete_path(incomplete, lambda p: _is_mka(p) and not _has_lyrics(p))
    if not any(item.type == "plain" and not item.value.endswith("/") for item in results):
        results = _complete_path(incomplete, _is_mka)
    return results


def _complete_mka_without_icon(ctx, param, incomplete):
    """Complete .mka files that lack a custom icon, falling back to all .mka."""
    results = _complete_path(incomplete, lambda p: _is_mka(p) and not _has_custom_icon(p))
    if not any(item.type == "plain" and not item.value.endswith("/") for item in results):
        results = _complete_path(incomplete, _is_mka)
    return results


# ── CLI group ─────────────────────────────────────────────────────────────────


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose (DEBUG) console output")
def cli(verbose):
    """Manage Yoto CYO playlists as folders on disk."""
    _setup_logging(verbose)


# ── Register command modules ─────────────────────────────────────────────────
# Importing these modules registers commands on the `cli` group via decorators.

import yoto_cli.commands.sync  # noqa: F401, E402
import yoto_cli.commands.pull  # noqa: F401, E402
import yoto_cli.commands.icons  # noqa: F401, E402
import yoto_cli.commands.cover  # noqa: F401, E402
import yoto_cli.commands.import_cmd  # noqa: F401, E402
import yoto_cli.commands.billing  # noqa: F401, E402
import yoto_cli.commands.lyrics  # noqa: F401, E402
import yoto_cli.commands.misc  # noqa: F401, E402
