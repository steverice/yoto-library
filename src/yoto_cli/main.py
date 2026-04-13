"""Yoto CLI — manage CYO playlists as folders on disk."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

import argcomplete
import click
from click.shell_completion import CompletionItem

if TYPE_CHECKING:
    from collections.abc import Callable
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

logger = logging.getLogger(__name__)

from yoto_lib.billing import persist_session  # noqa: E402
from yoto_lib.billing.costs import get_tracker  # noqa: E402
from yoto_lib.mka import read_tags  # noqa: E402


def _print_cost_summary() -> None:
    from yoto_cli.progress import _console

    tracker = get_tracker()
    if not tracker.has_records():
        return
    persist_session(tracker)
    for line in tracker.summary_lines():
        _console.print(f"[dim]{line}[/dim]")


class Formatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        description="Manage Yoto CYO playlists as folders on disk.",
        formatter_class=Formatter,
        allow_abbrev=False,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable verbose (DEBUG) console output")
    subparsers = parser.add_subparsers(dest="command")

    from yoto_cli.commands.billing import add_providers_command
    from yoto_cli.commands.misc import add_auth_command, add_init_command, add_list_command

    add_auth_command(subparsers)
    add_init_command(subparsers)
    add_list_command(subparsers)
    add_providers_command(subparsers)

    argcomplete.autocomplete(parser)
    return parser


def main() -> None:
    """CLI entry point — parse args and dispatch."""
    from yoto_cli.progress import error

    try:
        parser = build_parser()
        args, remaining = parser.parse_known_args()
        _setup_logging(getattr(args, "verbose", False))
        if hasattr(args, "func"):
            if remaining:
                parser.error(f"unrecognized arguments: {' '.join(remaining)}")
            args.func(args)
        else:
            cli(standalone_mode=True)
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — top-level CLI boundary
        error(str(exc))
        raise SystemExit(1) from None


def require_path(path: Path) -> None:
    """Raise SystemExit if path does not exist. Replaces click.Path(exists=True)."""
    if not path.exists():
        from yoto_cli.progress import error

        error(f"Path does not exist: {path}")
        raise SystemExit(2)


def _open_editor(content: str, suffix: str = ".jsonl") -> str | None:
    """Open content in $EDITOR. Returns edited text, or None if unchanged."""
    import tempfile

    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vi"))
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        subprocess.run([editor, str(tmp_path)], check=True)
        edited = tmp_path.read_text(encoding="utf-8")
        return edited if edited != content else None
    except subprocess.CalledProcessError:
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


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
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-5s %(name)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
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
    return bool(re.fullmatch(r"[A-Za-z0-9]{1,10}", value)) and not Path(value).exists()


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
            capture_output=True,
            text=True,
            timeout=5,
        )
        data = json.loads(result.stdout)
        return any(a.get("file_name") == "icon" for a in data.get("attachments", []))
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError, ValueError):
        return False


def _complete_path(incomplete: str, filter_fn: Callable[[Path], bool]) -> list[CompletionItem]:
    """Complete filesystem paths, yielding dirs (for navigation) and filtered files."""
    inc_path = Path(incomplete) if incomplete else Path()

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


def _complete_weblocs(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[CompletionItem]:
    """Complete .webloc file paths."""
    return _complete_path(incomplete, lambda p: p.suffix.lower() == ".webloc")


def _complete_dirs(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[CompletionItem]:
    """Complete directory paths only."""
    return _complete_path(incomplete, lambda _: False)


def _complete_unimported_dirs(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[CompletionItem]:
    """Complete directories that lack a playlist.jsonl, falling back to all dirs."""
    results = _complete_path(
        incomplete,
        lambda p: False,  # no files, dirs only
    )
    # Filter to dirs without playlist.jsonl
    filtered = [
        item for item in results if not item.value.endswith("/") or not (Path(item.value) / "playlist.jsonl").exists()
    ]
    return filtered if any(item.value.endswith("/") for item in filtered) else results


def _is_mka(p: Path) -> bool:
    return p.suffix.lower() == ".mka"


def _complete_mka_with_icon(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[CompletionItem]:
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
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError):
        return False


def _complete_lyrics_path(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[CompletionItem]:
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


def _complete_mka_without_icon(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[CompletionItem]:
    """Complete .mka files that lack a custom icon, falling back to all .mka."""
    results = _complete_path(incomplete, lambda p: _is_mka(p) and not _has_custom_icon(p))
    if not any(item.type == "plain" and not item.value.endswith("/") for item in results):
        results = _complete_path(incomplete, _is_mka)
    return results


# ── CLI group ─────────────────────────────────────────────────────────────────


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose (DEBUG) console output")
def cli(verbose: bool) -> None:
    """Manage Yoto CYO playlists as folders on disk."""
    _setup_logging(verbose)


# ── Register command modules ─────────────────────────────────────────────────
# Importing these modules registers commands on the `cli` group via decorators.

import yoto_cli.commands.cover  # noqa: E402
import yoto_cli.commands.icons  # noqa: E402
import yoto_cli.commands.import_cmd  # noqa: E402
import yoto_cli.commands.lyrics  # noqa: E402
import yoto_cli.commands.misc  # noqa: E402
import yoto_cli.commands.pull  # noqa: E402
import yoto_cli.commands.sync  # noqa: F401, E402
