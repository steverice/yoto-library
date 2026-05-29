"""cover and print commands."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

    from rich.progress import TaskID

from yoto_cli.main import _print_cost_summary
from yoto_lib.billing.costs import reset_tracker
from yoto_lib.covers.cover import generate_cover_if_missing
from yoto_lib.covers.printer import PrintError, print_cover
from yoto_lib.covers.styles import CoverStyle
from yoto_lib.description import generate_description
from yoto_lib.playlist import load_playlist

logger = logging.getLogger(__name__)


def add_cover_command(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser("cover", help="generate cover art for a playlist folder")
    sub.add_argument("path", nargs="?", default=".", type=Path, help="playlist folder")
    mode = sub.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="regenerate even if cover.png exists")
    mode.add_argument("--backup", action="store_true", help="rename existing cover.png before generating")
    sub.add_argument("--ignore-album-art", action="store_true", help="skip album art reuse")
    sub.add_argument(
        "--style",
        type=str.lower,
        choices=sorted(CoverStyle.names()),
        default=None,
        help="visual art style for the cover",
    )
    sub.add_argument(
        "--model",
        type=str.lower,
        choices=["gemini", "openai"],
        default=None,
        help="image model for the illustration; when unset, uses gemini with --reference, else openai",
    )
    sub.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="image to use as inspiration for the illustration (requires the Gemini model)",
    )
    sub.set_defaults(func=handle_cover)


def _read_reference_image(reference: Path) -> bytes:
    """Validate that reference is a readable image and return its bytes, or exit."""
    import io

    from PIL import Image

    from yoto_cli.main import require_path
    from yoto_cli.progress import error as _error

    require_path(reference)
    try:
        data = reference.read_bytes()
        Image.open(io.BytesIO(data)).verify()
    except OSError:  # includes PIL.UnidentifiedImageError
        _error(f"Not a valid image: {reference}")
        raise SystemExit(2) from None
    return data


def handle_cover(args: argparse.Namespace) -> None:
    """Generate cover art for a playlist folder."""
    from yoto_cli.main import require_path

    require_path(args.path)
    path: Path = args.path
    force: bool = args.force
    backup: bool = args.backup
    ignore_album_art: bool = args.ignore_album_art
    style: str | None = args.style
    model: str | None = getattr(args, "model", None)
    reference: Path | None = getattr(args, "reference", None)

    logger.debug("command: cover path=%s force=%s backup=%s ignore_album_art=%s", path, force, backup, ignore_album_art)
    reset_tracker()
    import tempfile

    from yoto_cli.progress import _console as rich_console
    from yoto_cli.progress import error as _error
    from yoto_lib import mka
    from yoto_lib.covers.cover import (
        add_title_to_illustration,
        build_cover_prompt,
        generate_cover_illustration,
        resize_cover,
        resolve_cover_model,
        try_shared_album_art,
    )

    folder = path
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

    # Validate reference image and resolve the illustration provider up front, so any
    # configuration error is reported before generation work starts.
    reference_bytes = _read_reference_image(reference) if reference is not None else None
    try:
        cover_model = resolve_cover_model(model, has_reference=reference_bytes is not None)
        if playlist.title and not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("adding a cover title requires OPENAI_API_KEY")
    except ValueError as exc:
        _error(str(exc))
        raise SystemExit(2) from None

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
        progress.update(task, completed=recompose_steps, status=f"generating cover art ({resolved_style.name})")

        track_titles: list[str] = []
        artists: list[str] = []
        for filename in playlist.track_files:
            track_path = folder / filename
            try:
                tags = mka.read_tags(track_path)
                title = tags.get("title") or Path(filename).stem
                artist = tags.get("artist", "")
            except (subprocess.CalledProcessError, OSError, json.JSONDecodeError):
                title = Path(filename).stem
                artist = ""
            track_titles.append(title)
            if artist:
                artists.append(artist)

        prompt = build_cover_prompt(playlist.description, track_titles, artists, playlist.title, style=resolved_style)

        # Step 1 at 1024x1536 (~0.667) -- only ~28px cropped per side to reach 638:1011.
        inner = progress.add_task(f"Generating cover art ({cover_model})", total=None, status="")
        image_bytes = generate_cover_illustration(prompt, model=cover_model, reference_image=reference_bytes)
        progress.remove_task(inner)
        progress.update(task, advance=1, status="generated cover art")

        if playlist.title:
            progress.update(task, advance=1, status="adding title")
            inner = progress.add_task("Adding title", total=None, status="")
            image_bytes = add_title_to_illustration(
                image_bytes, playlist.title, 1024, 1536, style=resolved_style, high_fidelity=(cover_model == "gemini")
            )
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


def add_print_command(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser("print", help="print cover art to a photo printer")
    sub.add_argument("path", nargs="?", default=".", type=Path, help="playlist folder")
    sub.add_argument("--yes", "-y", action="store_true", help="skip confirmation prompt")
    sub.add_argument("--profile", default=None, type=Path, help="ICC color profile for the printer")
    sub.set_defaults(func=handle_print)


def handle_print(args: argparse.Namespace) -> None:
    """Print cover art to a photo printer."""
    from rich.prompt import Confirm

    from yoto_cli.main import require_path

    require_path(args.path)
    path: Path = args.path
    yes: bool = args.yes
    profile: Path | None = args.profile

    logger.debug("command: print path=%s yes=%s profile=%s", path, yes, profile)
    from yoto_cli.progress import _console
    from yoto_cli.progress import error as _error
    from yoto_cli.progress import success as _success
    from yoto_cli.progress import warning as _warning

    folder = path
    playlist = load_playlist(folder)
    cover_path = playlist.cover_path

    if not cover_path.exists():
        if not Confirm.ask("No cover found. Generate one?", default=False, console=_console):
            return
        generate_cover_if_missing(playlist, log=lambda msg: _console.print(msg))
        # Reload -- generation may have created cover.png
        if not cover_path.exists():
            _error("Cover generation failed.")
            raise SystemExit(1)

    # Resolve ICC profile: --profile flag > env var > None (skip)
    icc_profile: str | None = str(profile) if profile else os.environ.get("YOTO_ICC_PROFILE")
    if icc_profile and not Path(icc_profile).exists():
        _warning(f"ICC profile not found: {icc_profile}")
        if not Confirm.ask("Continue without color management?", default=True, console=_console):
            return
        icc_profile = None

    if not yes:
        title = playlist.title or folder.name
        if not Confirm.ask(f"Print cover for '{title}'?", default=False, console=_console):
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
        _error(str(exc))
        raise SystemExit(1) from exc

    _success("Print complete")
