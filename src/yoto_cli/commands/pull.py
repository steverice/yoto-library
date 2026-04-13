"""pull command."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

    from rich.progress import TaskID

from yoto_cli.main import _is_card_id
from yoto_lib.pull import pull_playlist
from yoto_lib.yoto.api import YotoAPI

logger = logging.getLogger(__name__)


def add_pull_command(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser("pull", help="pull remote playlist state to local")
    sub.add_argument("path_or_card_id", nargs="?", default=".", help="folder path or card ID")
    sub.add_argument("--dry-run", action="store_true", help="preview changes without executing")
    sub.add_argument(
        "--all", dest="pull_all", action="store_true", help="pull all playlists into subdirectories of cwd"
    )
    sub.set_defaults(func=handle_pull)


def handle_pull(args: argparse.Namespace) -> None:
    """Pull remote playlist state to local."""
    logger.debug(
        "command: pull path_or_card_id=%s dry_run=%s all=%s", args.path_or_card_id, args.dry_run, args.pull_all
    )
    if args.pull_all:
        _pull_all(dry_run=args.dry_run)
        return

    if _is_card_id(args.path_or_card_id):
        folder = Path()
        card_id = args.path_or_card_id
    else:
        folder = Path(args.path_or_card_id)
        card_id = None

    _pull_one(folder, card_id=card_id, dry_run=args.dry_run)


def _pull_one(folder: Path, card_id: str | None = None, dry_run: bool = False) -> None:
    """Pull a single playlist."""
    if sys.stderr.isatty():
        from yoto_cli.progress import make_progress

        with make_progress() as progress:
            task = progress.add_task(folder.name, total=None, status="fetching")
            inner_tasks: dict[str, TaskID] = {}  # title -> task id

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
                folder,
                card_id=card_id,
                dry_run=dry_run,
                on_track_done=on_track,
                on_total=on_total,
                on_track_start=on_track_start,
                on_download_progress=on_download_progress,
            )
    else:
        from yoto_cli.progress import _console as _con

        def on_track(title: str) -> None:
            _con.print(f"  Downloaded: {title}")

        result = pull_playlist(folder, card_id=card_id, dry_run=dry_run, on_track_done=on_track)

    from yoto_cli.progress import _console
    from yoto_cli.progress import error as _error
    from yoto_cli.progress import success as _success

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
        folder = Path(title)
        folder.mkdir(exist_ok=True)
        _pull_one(folder, card_id=card_id, dry_run=dry_run)
