"""lyrics command."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import click

from yoto_lib.lyrics import get_lyrics
from yoto_lib.mka import read_tags, write_tags

from yoto_cli.main import cli, _complete_lyrics_path

logger = logging.getLogger(__name__)


@cli.command()
@click.argument("path", type=click.Path(), required=False, default=None, shell_complete=_complete_lyrics_path)
@click.option("--force", is_flag=True, help="Re-fetch lyrics even if already present / skip confirmation for stdin")
@click.option("--show", is_flag=True, help="Display stored lyrics for each track")
@click.option("--add-source", "add_source_url", default=None, metavar="URL",
              help="Analyze a lyrics website and generate a scraping config.")
def lyrics(path: str | None, force: bool, show: bool, add_source_url: str | None) -> None:
    """Fetch and store lyrics for tracks in a playlist folder or single track.

    Accepts a playlist folder or a single .mka file. When piping lyrics via
    stdin (e.g. `cat lyrics.txt | yoto lyrics track.mka`), requires a single
    track path. Use --force to skip confirmation when overwriting.
    """
    logger.debug("command: lyrics path=%s force=%s show=%s add_source_url=%s", path, force, show, add_source_url)
    from yoto_cli.progress import _console, error as _error, success as _success
    from rich.prompt import Confirm

    if add_source_url:
        import shutil
        import subprocess
        import json

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
            from yoto_lib.lyrics.lyrics_source_wizard import run_wizard
            from yoto_cli.progress import make_progress
            _WIZARD_STEPS = 6
            with make_progress() as progress:
                task = progress.add_task("Analyzing lyrics site", total=_WIZARD_STEPS, status="")

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
        preview_text = (
            f"Sample: {sample_song}\n\n"
            f"{sample_lyrics[:500]}{'...' if len(sample_lyrics) > 500 else ''}"
        )
        _console.print(Panel(
            Text(preview_text),
            title=config["name"],
            subtitle=config["url"],
            border_style="cyan",
            padding=(1, 2),
            width=min(100, _console.width),
        ))

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

    target = Path(path)

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
                _console.print(Panel(
                    Text(text),
                    title=title,
                    subtitle=subtitle,
                    border_style="cyan",
                    padding=(1, 2),
                    width=min(120, _console.width),
                ))
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
