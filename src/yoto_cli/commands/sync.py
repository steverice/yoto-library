"""sync and status commands."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from rich.progress import TaskID

from yoto_cli.main import _print_cost_summary, cli
from yoto_lib.billing.costs import reset_tracker
from yoto_lib.covers.printer import PrintError, print_cover
from yoto_lib.playlist import diff_playlists, load_playlist, scan_audio_files
from yoto_lib.sync import sync_path
from yoto_lib.yoto.api import YotoAPI

logger = logging.getLogger(__name__)


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Preview changes without executing")
@click.option("--no-trim", is_flag=True, help="Skip silence trimming on YouTube downloads")
@click.option("--ignore-album-art", is_flag=True, help="Skip album art reuse; generate cover purely from prompt")
@click.option("--force-cover", is_flag=True, help="Re-upload cover art even if unchanged")
@click.option("--print/--no-print", "print_cover_flag", default=None, help="Print cover art after sync")
def sync(path, dry_run, no_trim, ignore_album_art, force_cover, print_cover_flag):
    """Push local playlist state to Yoto."""
    logger.debug(
        "command: sync path=%s dry_run=%s no_trim=%s ignore_album_art=%s", path, dry_run, no_trim, ignore_album_art
    )
    trim = not no_trim
    reset_tracker()
    from yoto_cli.progress import _console
    from yoto_cli.progress import error as _error

    if dry_run:
        results = sync_path(
            Path(path), dry_run=True, trim=trim, ignore_album_art=ignore_album_art, force_cover=force_cover
        )
        for result in results:
            icon_msg = f", {result.icons_uploaded} icons" if result.icons_uploaded else ""
            _console.print(f"[Dry run] Would upload {result.tracks_uploaded} tracks{icon_msg}")
            for err in result.errors:
                _error(err)
        return

    from yoto_cli.progress import make_progress
    from yoto_lib.playlist import load_playlist as _load

    playlist = _load(Path(path))
    total = len(playlist.track_files) * 2 + 2

    with make_progress() as progress:
        task = progress.add_task(playlist.title, total=total, status="starting")
        upload_tasks: dict[str, TaskID] = {}  # filename -> inner task id

        def log(msg: str) -> None:
            progress.update(task, advance=1, status=msg)
            progress.console.print(msg)

        def on_upload_start(filename: str) -> None:
            stem = Path(filename).stem
            inner_task = progress.add_task(stem, total=None, status="uploading")
            upload_tasks[filename] = inner_task

        def on_upload_done(filename: str) -> None:
            inner_task = upload_tasks.pop(filename, None)
            if inner_task is not None:
                progress.remove_task(inner_task)
            progress.update(task, advance=1)

        results = sync_path(
            Path(path),
            dry_run=False,
            trim=trim,
            log=log,
            on_upload_start=on_upload_start,
            on_upload_done=on_upload_done,
            ignore_album_art=ignore_album_art,
            force_cover=force_cover,
        )
        progress.update(task, completed=total)

    from yoto_cli.progress import error as _error
    from yoto_cli.progress import success as _success

    for result in results:
        icon_msg = f", {result.icons_uploaded} icons" if result.icons_uploaded else ""
        _success(f"card {result.card_id}: {result.tracks_uploaded} tracks{icon_msg}")
        for err in result.errors:
            _error(err)
    _print_cost_summary()

    # Offer to print cover if it was newly generated/uploaded
    if not dry_run:
        from yoto_cli.progress import warning as _warning

        for result in results:
            if not result.cover_uploaded or not result.folder:
                continue
            cover_path = result.folder / "cover.png"
            if not cover_path.exists():
                continue

            should_print = print_cover_flag
            if should_print is None:
                should_print = click.confirm("Cover was generated. Print it?", default=False)
            if should_print:
                icc_profile = os.environ.get("YOTO_ICC_PROFILE")
                if icc_profile and not Path(icc_profile).exists():
                    _warning(f"ICC profile not found: {icc_profile}")
                    icc_profile = None
                try:
                    _console.print("[dim]Ctrl+C to stop waiting (won't cancel the print)[/dim]")
                    with _console.status("Sending to printer...", spinner="dots") as status:
                        print_cover(
                            cover_path,
                            icc_profile=icc_profile,
                            on_status=lambda msg: status.update(f"Printing: {msg}"),
                        )
                    _success("Print complete")
                except PrintError as exc:
                    _warning(f"Print failed: {exc}")


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def status(path):
    """Show diff between local and remote state."""
    logger.debug("command: status path=%s", path)
    folder = Path(path)
    if not (folder / "playlist.jsonl").exists() and not scan_audio_files(folder):
        raise click.ClickException("Not a playlist folder (no playlist.jsonl or audio files)")
    playlist = load_playlist(folder)

    from yoto_cli.progress import _console
    from yoto_cli.progress import warning as _warning

    remote_state = None
    if playlist.card_id:
        try:
            api = YotoAPI()
            remote_content = api.get_content(playlist.card_id)
            from yoto_lib.sync import _parse_remote_state

            remote_state = _parse_remote_state(remote_content)
        except Exception as exc:
            _warning(f"could not fetch remote state: {exc}")

    diff = diff_playlists(playlist, remote_state)

    if not any([diff.new_tracks, diff.removed_tracks, diff.order_changed, diff.cover_changed, diff.metadata_changed]):
        _console.print("[dim]No changes.[/dim]")
        return

    if diff.new_tracks:
        for t in diff.new_tracks:
            _console.print(f"  [green]+[/green] {t}")
    if diff.removed_tracks:
        for t in diff.removed_tracks:
            _console.print(f"  [red]-[/red] {t}")
    if diff.order_changed:
        _console.print("  [yellow]~[/yellow] track order changed")
    if diff.cover_changed:
        _console.print("  [yellow]~[/yellow] cover changed")
    if diff.metadata_changed:
        _console.print("  [yellow]~[/yellow] metadata changed")
