"""cover and print commands."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from rich.progress import TaskID

from yoto_cli.main import _complete_dirs, _print_cost_summary, cli
from yoto_lib.billing.costs import reset_tracker
from yoto_lib.covers.cover import generate_cover_if_missing
from yoto_lib.covers.printer import PrintError, print_cover
from yoto_lib.covers.styles import CoverStyle
from yoto_lib.description import generate_description
from yoto_lib.playlist import load_playlist

logger = logging.getLogger(__name__)


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True), shell_complete=_complete_dirs)
@click.option("--force", is_flag=True, help="Regenerate even if cover.png exists")
@click.option("--backup", is_flag=True, help="Like --force, but rename existing cover.png first")
@click.option("--ignore-album-art", is_flag=True, help="Skip album art reuse; generate cover purely from prompt")
@click.option(
    "--style",
    type=click.Choice(sorted(CoverStyle.names()), case_sensitive=False),
    default=None,
    help="Visual art style for the cover",
)
def cover(path, force, backup, ignore_album_art, style):
    """Generate cover art for a playlist folder."""
    if force and backup:
        raise click.UsageError("--force and --backup are mutually exclusive")
    logger.debug("command: cover path=%s force=%s backup=%s ignore_album_art=%s", path, force, backup, ignore_album_art)
    reset_tracker()
    import tempfile

    from yoto_cli.progress import _console as rich_console
    from yoto_lib import mka
    from yoto_lib.covers.cover import add_title_to_illustration, build_cover_prompt, resize_cover, try_shared_album_art
    from yoto_lib.providers import get_provider

    folder = Path(path)
    playlist = load_playlist(folder)
    cover_path = playlist.cover_path

    if cover_path.exists() and backup:
        # Find next available backup name
        n = 1
        while (backup_path := cover_path.with_name(f"cover.{n}.png")).exists():
            n += 1
        cover_path.rename(backup_path)
        rich_console.print(f"Backed up existing cover to {backup_path.name}")
    elif cover_path.exists() and not force:
        rich_console.print(f"Cover already exists: {cover_path}")
        rich_console.print("Use --force to regenerate, or --backup to keep the old one.")
        return

    # Generate description if missing (interactive)
    if not playlist.description_path.exists():
        from rich.prompt import Prompt as _Prompt

        generate_description(
            playlist,
            log=lambda msg: rich_console.print(msg),
            ask_user=lambda q: _Prompt.ask(q, console=rich_console),
        )

    # Resolve style: --style flag > .yoto-style file > default
    if style:
        playlist.style_path.write_text(style + "\n", encoding="utf-8")
        rich_console.print(f"Style set to: {style}")
    resolved_style = CoverStyle.get(playlist.style)

    from yoto_cli.progress import make_progress
    from yoto_cli.progress import success as _success
    from yoto_lib.covers.cover import RECOMPOSE_MAX_ATTEMPTS

    cover_name = playlist.title or folder.name
    title_steps = 1 if playlist.title else 0
    # Worst case: all recompose attempts + generate + optional title + save
    recompose_steps = 0 if ignore_album_art else RECOMPOSE_MAX_ATTEMPTS
    total_steps = recompose_steps + 1 + title_steps + 1

    with make_progress() as progress:
        initial_status = f"generating cover art ({resolved_style.name})" if ignore_album_art else "checking album art"
        task = progress.add_task(cover_name, total=total_steps, status=initial_status)

        # Tracks the current inner task for nested progress
        _inner_task: list[TaskID | None] = [None]

        def _cover_log(msg: str) -> None:
            if msg.startswith("WARNING:"):
                progress.console.print(f"[yellow]-- {msg}[/yellow]")
            else:
                progress.update(task, status=msg)

        def _cover_step() -> None:
            progress.update(task, advance=1)

        def _cover_inner(status: str | None, step: int | None, total: int | None) -> None:
            if status is None:
                # Remove the inner task
                if _inner_task[0] is not None:
                    progress.remove_task(_inner_task[0])
                    _inner_task[0] = None
            elif _inner_task[0] is None:
                # Create a new inner task
                _inner_task[0] = progress.add_task(status, total=total, status="")
            else:
                # Update the existing inner task
                progress.update(
                    _inner_task[0], description=status, completed=step if step is not None else 0, total=total
                )

        # Try reusing shared album art first
        if not ignore_album_art and try_shared_album_art(
            playlist, log=_cover_log, on_step=_cover_step, on_inner=_cover_inner
        ):
            progress.update(task, completed=total_steps)
            progress.stop()
            _success(f"Reused album art as cover: {cover_path}")
            _print_cost_summary()
            return

        # No shared art -- generate from scratch
        progress.update(task, completed=recompose_steps, status="generating cover art")

        track_titles: list[str] = []
        artists: list[str] = []
        for filename in playlist.track_files:
            track_path = folder / filename
            try:
                tags = mka.read_tags(track_path)
                title = tags.get("title") or Path(filename).stem
                artist = tags.get("artist", "")
            except Exception:
                title = Path(filename).stem
                artist = ""
            track_titles.append(title)
            if artist:
                artists.append(artist)

        prompt = build_cover_prompt(playlist.description, track_titles, artists, playlist.title, style=resolved_style)

        provider = get_provider()
        # Request 1024x1536 -- maps exactly to that OpenAI size (~0.667),
        # only ~28px cropped per side to reach 638:1011 (~0.631) target.
        inner = progress.add_task("Generating cover art", total=None, status="")
        image_bytes = provider.generate(prompt, 1024, 1536, quality="low")
        progress.remove_task(inner)
        progress.update(task, advance=1, status="generated cover art")

        if playlist.title:
            progress.update(task, advance=1, status="adding title")
            inner = progress.add_task("Adding title", total=None, status="")
            image_bytes = add_title_to_illustration(image_bytes, playlist.title, 1024, 1536, style=resolved_style)
            progress.remove_task(inner)
            progress.update(task, status="title added")

        progress.update(task, status="saving")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = Path(tmp.name)

        try:
            resize_cover(tmp_path, cover_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        progress.update(task, advance=1)

    _success(f"Saved cover to {cover_path}")
    _print_cost_summary()


@cli.command(name="print")
@click.argument("path", default=".", type=click.Path(exists=True), shell_complete=_complete_dirs)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option("--profile", type=click.Path(), default=None, help="ICC color profile for the printer")
def print_cmd(path, yes, profile):
    """Print cover art to a photo printer."""
    logger.debug("command: print path=%s yes=%s profile=%s", path, yes, profile)
    from yoto_cli.progress import _console
    from yoto_cli.progress import success as _success
    from yoto_cli.progress import warning as _warning

    folder = Path(path)
    playlist = load_playlist(folder)
    cover_path = playlist.cover_path

    if not cover_path.exists():
        if not click.confirm("No cover found. Generate one?", default=False):
            return
        generate_cover_if_missing(playlist, log=lambda msg: _console.print(msg))
        # Reload -- generation may have created cover.png
        if not cover_path.exists():
            raise click.ClickException("Cover generation failed.")

    # Resolve ICC profile: --profile flag > env var > None (skip)
    icc_profile = profile or os.environ.get("YOTO_ICC_PROFILE")
    if icc_profile and not Path(icc_profile).exists():
        _warning(f"ICC profile not found: {icc_profile}")
        if not click.confirm("Continue without color management?", default=True):
            return
        icc_profile = None

    if not yes:
        title = playlist.title or folder.name
        if not click.confirm(f"Print cover for '{title}'?", default=False):
            return

    try:
        _console.print("[dim]Ctrl+C to stop waiting (won't cancel the print)[/dim]")
        with _console.status("Sending to printer...", spinner="dots") as status:
            print_cover(
                cover_path,
                icc_profile=icc_profile,
                on_status=lambda msg: status.update(f"Printing: {msg}"),
            )
    except PrintError as exc:
        raise click.ClickException(str(exc)) from exc

    _success("Print complete")
