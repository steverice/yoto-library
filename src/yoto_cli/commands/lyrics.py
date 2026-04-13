"""lyrics command."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

from yoto_lib.lyrics import get_lyrics
from yoto_lib.mka import read_tags, write_tags

logger = logging.getLogger(__name__)


def add_lyrics_command(subparsers: argparse._SubParsersAction) -> None:
    """Register the lyrics subcommand."""
    from yoto_cli.main import _LyricsPathCompleter

    sub = subparsers.add_parser("lyrics", help="fetch and store lyrics for tracks")
    sub.add_argument(
        "path", nargs="?", default=None, type=Path, help="playlist folder or .mka file"
    ).completer = _LyricsPathCompleter()
    sub.add_argument("--force", action="store_true", help="re-fetch even if already present")
    sub.add_argument("--show", action="store_true", help="display stored lyrics")
    sub.add_argument("--clear", action="store_true", help="remove stored lyrics")
    sub.add_argument(
        "--add-source",
        dest="add_source_url",
        default=None,
        metavar="URL",
        help="analyze a lyrics website and generate a scraping config",
    )
    sub.set_defaults(func=handle_lyrics)


def handle_lyrics(args: argparse.Namespace) -> None:
    """Fetch and store lyrics for tracks in a playlist folder or single track."""
    path: Path | None = args.path
    force: bool = args.force
    show: bool = args.show
    clear: bool = args.clear
    add_source_url: str | None = args.add_source_url

    logger.debug(
        "command: lyrics path=%s force=%s show=%s clear=%s add_source_url=%s",
        path,
        force,
        show,
        clear,
        add_source_url,
    )
    from rich.prompt import Confirm

    from yoto_cli.progress import _console
    from yoto_cli.progress import error as _error
    from yoto_cli.progress import success as _success

    if add_source_url:
        import json
        import shutil
        import subprocess

        # 1. Check node is on PATH
        if not shutil.which("node"):
            _error("'node' not found on PATH. Install Node.js (v18+) to use lyrics scraping.")
            return

        # 2. Check jsdom is installed (run from yoto_lib dir so Node resolves local node_modules)
        jsdom_check = subprocess.run(
            ["node", "-e", "require('jsdom')"],
            capture_output=True,
            cwd=Path(__file__).parent.parent.parent / "yoto_lib",
        )
        if jsdom_check.returncode != 0:
            _error("jsdom not installed. Run: npm install jsdom")
            return

        # 3. Run the wizard
        try:
            from yoto_cli.progress import make_progress
            from yoto_lib.lyrics.lyrics_source_wizard import run_wizard

            wizard_steps = 6
            with make_progress() as progress:
                task = progress.add_task("Analyzing lyrics site", total=wizard_steps, status="")

                def _on_step(msg: str) -> None:
                    progress.update(task, advance=1, status=msg)

                config = run_wizard(add_source_url, on_step=_on_step)
        except ValueError as exc:
            _error(str(exc))
            return

        # 4. Show preview
        from rich.panel import Panel
        from rich.text import Text

        sample_lyrics = config.get("_sample_lyrics", "")
        sample_song = config.get("_sample_song", "(unknown)")
        preview_text = f"Sample: {sample_song}\n\n{sample_lyrics[:500]}{'...' if len(sample_lyrics) > 500 else ''}"
        _console.print(
            Panel(
                Text(preview_text),
                title=config["name"],
                subtitle=config["url"],
                border_style="cyan",
                padding=(1, 2),
                width=min(100, _console.width),
            )
        )

        # 5. Confirm
        if not Confirm.ask("Save this lyrics source config?", console=_console):
            return

        # 6. Save the config
        lyrics_dir = Path.home() / ".yoto" / "lyrics"
        lyrics_dir.mkdir(parents=True, exist_ok=True)

        slug = re.sub(r"[^\w\s-]", "", config["name"].lower())
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")
        config_path = lyrics_dir / f"{slug}.json"

        save_config = {k: v for k, v in config.items() if not k.startswith("_")}
        with config_path.open("w", encoding="utf-8") as f:
            json.dump(save_config, f, indent=2)

        # 7. Report success
        _success(f"Saved to ~/.yoto/lyrics/{slug}.json")
        _console.print("Use 'yoto lyrics --force <playlist>' to fetch lyrics using the new source.")
        return

    if path is None:
        _error("Missing argument 'PATH'. Run 'yoto lyrics --help' for usage.")
        return

    target = path

    # Resolve target to a list of MKA files
    if target.is_file() and target.suffix.lower() == ".mka":
        mka_files = [target]
    elif target.is_dir():
        mka_files = sorted(target.glob("*.mka"))
    else:
        _console.print(f"[red]x[/red] {target}: not a playlist folder or .mka file")
        return

    if not mka_files:
        _console.print("[dim]No MKA files found.[/dim]")
        return

    # Check for stdin input (piped lyrics)
    has_stdin = not sys.stdin.isatty()
    if has_stdin:
        if len(mka_files) != 1 or target.is_dir():
            _console.print("[red]x[/red] When piping lyrics via stdin, specify a single .mka file")
            return
        stdin_text = sys.stdin.read()
        if not stdin_text.strip():
            _console.print("[red]x[/red] No lyrics on stdin")
            return
        mka_path = mka_files[0]
        tags = read_tags(mka_path)
        if tags.get("lyrics") and not force:
            _console.print(f"[bold]{mka_path.name}[/bold] already has lyrics:")
            _console.print(f"[dim]{tags['lyrics'][:200]}{'...' if len(tags['lyrics']) > 200 else ''}[/dim]")
            if not Confirm.ask("Overwrite?", console=_console):
                return
        write_tags(mka_path, {"lyrics": stdin_text, "lyrics_summary": ""})
        _console.print(f"  {mka_path.name}: lyrics set from stdin")
        return

    if show:
        from rich.panel import Panel
        from rich.text import Text

        with _console.pager(styles=True):
            for mka_path in mka_files:
                tags = read_tags(mka_path)
                title = tags.get("title", mka_path.stem)
                artist = tags.get("artist", "")
                text = tags.get("lyrics")
                if not text:
                    _console.print(f"[dim]{mka_path.name}: no lyrics stored[/dim]")
                    continue
                subtitle = artist if artist else None
                _console.print(
                    Panel(
                        Text(text),
                        title=title,
                        subtitle=subtitle,
                        border_style="cyan",
                        padding=(1, 2),
                        width=min(120, _console.width),
                    )
                )
        return

    if clear:
        for mka_path in mka_files:
            tags = read_tags(mka_path)
            if not tags.get("lyrics"):
                _console.print(f"  [dim]{mka_path.name}: no lyrics to clear[/dim]")
                continue
            write_tags(mka_path, {"lyrics": "", "lyrics_summary": ""})
            _console.print(f"  {mka_path.name}: lyrics cleared")
        return

    from yoto_cli.progress import make_progress

    with make_progress() as progress:
        task = progress.add_task(target.name, total=len(mka_files), status="")
        for mka_path in mka_files:
            progress.update(task, status=mka_path.stem)
            tags = read_tags(mka_path)

            if not force and tags.get("lyrics"):
                progress.console.print(f"  [dim]{mka_path.name}: lyrics already present[/dim]")
                progress.update(task, advance=1)
                continue

            # Build a tags dict that get_lyrics can use (needs title, artist)
            lookup_tags = {
                "title": tags.get("title", mka_path.stem),
                "artist": tags.get("artist", ""),
            }
            # Only include existing lyrics when not forcing re-fetch
            if not force and "lyrics" in tags:
                lookup_tags["lyrics"] = tags["lyrics"]

            lyrics_text, lyrics_source = get_lyrics(lookup_tags)
            if lyrics_text:
                new_tags = {"lyrics": lyrics_text}
                if force:
                    # Clear stale summary so it's regenerated on next select-icon
                    new_tags["lyrics_summary"] = ""
                write_tags(mka_path, new_tags)
                progress.console.print(f"  {mka_path.name}: lyrics found via {lyrics_source}")
            else:
                progress.console.print(f"  [dim]{mka_path.name}: no lyrics found[/dim]")
            progress.update(task, advance=1)
