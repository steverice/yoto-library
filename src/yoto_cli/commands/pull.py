"""pull command."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from yoto_lib.pull import pull_playlist
from yoto_lib.yoto.api import YotoAPI

from yoto_cli.main import cli, _is_card_id

logger = logging.getLogger(__name__)


@cli.command()
@click.argument("path_or_card_id", default=".")
@click.option("--dry-run", is_flag=True, help="Preview changes without executing")
@click.option("--all", "pull_all", is_flag=True, help="Pull all playlists into subdirectories of cwd")
def pull(path_or_card_id, dry_run, pull_all):
    """Pull remote playlist state to local."""
    logger.debug("command: pull path_or_card_id=%s dry_run=%s all=%s", path_or_card_id, dry_run, pull_all)
    if pull_all:
        _pull_all(dry_run=dry_run)
        return

    if _is_card_id(path_or_card_id):
        folder = Path(".")
        card_id = path_or_card_id
    else:
        folder = Path(path_or_card_id)
        card_id = None

    _pull_one(folder, card_id=card_id, dry_run=dry_run)


def _pull_one(folder: Path, card_id: str | None = None, dry_run: bool = False) -> None:
    """Pull a single playlist."""
    if sys.stderr.isatty():
        from yoto_cli.progress import make_progress
        with make_progress() as progress:
            task = progress.add_task(folder.name, total=None, status="fetching")
            inner_tasks: dict[str, int] = {}  # title -> task id

            def on_total(n: int) -> None:
                progress.update(task, total=n, status="downloading")

            def on_track_start(title: str) -> None:
                # total=None -> indeterminate until we get content-length
                inner_task = progress.add_task(title, total=None, status="")
                inner_tasks[title] = inner_task

            def on_download_progress(title: str, downloaded: int, total: int | None) -> None:
                inner_task = inner_tasks.get(title)
                if inner_task is not None:
                    if total is not None:
                        progress.update(inner_task, completed=downloaded, total=total, status="")
                    else:
                        progress.update(inner_task, completed=downloaded, status="")

            def on_track(title: str) -> None:
                progress.update(task, advance=1, status=title)
                inner_task = inner_tasks.pop(title, None)
                if inner_task is not None:
                    progress.remove_task(inner_task)

            result = pull_playlist(
                folder, card_id=card_id, dry_run=dry_run,
                on_track_done=on_track, on_total=on_total,
                on_track_start=on_track_start,
                on_download_progress=on_download_progress,
            )
    else:
        from yoto_cli.progress import _console as _con
        def on_track(title: str) -> None:
            _con.print(f"  Downloaded: {title}")
        result = pull_playlist(folder, card_id=card_id, dry_run=dry_run, on_track_done=on_track)

    from yoto_cli.progress import _console, success as _success, error as _error
    if dry_run:
        _console.print(f"[Dry run] {result.card_id}")
    else:
        icon_msg = f", {result.icons_downloaded} icons" if result.icons_downloaded else ""
        _success(f"{result.card_id}: {result.tracks_downloaded} tracks{icon_msg}")
    for err in result.errors:
        _error(err)


def _pull_all(dry_run: bool = False) -> None:
    """Pull every playlist on the account into a subdirectory of cwd."""
    api = YotoAPI()
    cards = api.get_my_content()

    from yoto_cli.progress import _console
    if not cards:
        _console.print("[dim]No cards found.[/dim]")
        return

    for card in cards:
        card_id = card.get("cardId", "")
        title = card.get("title", card_id)
        _console.print(f"Pulling {title}...")
        folder = Path(".") / title
        folder.mkdir(exist_ok=True)
        _pull_one(folder, card_id=card_id, dry_run=dry_run)
