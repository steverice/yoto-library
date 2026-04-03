"""Yoto CLI — manage CYO playlists as folders on disk."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from yoto_lib.auth import AuthError, run_device_code_flow
from yoto_lib.api import YotoAPI
from yoto_lib.sync import sync_path
from yoto_lib.pull import pull_playlist
from yoto_lib.playlist import read_jsonl, write_jsonl, scan_audio_files, load_playlist, diff_playlists
from yoto_lib.mka import wrap_in_mka


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_card_id(value: str) -> bool:
    """
    Heuristic: treat as card_id if it is a short (≤10 chars) alphanumeric
    string that does NOT exist as a path on disk.
    """
    return (
        bool(re.fullmatch(r"[A-Za-z0-9]{1,10}", value))
        and not Path(value).exists()
    )


# ── CLI group ─────────────────────────────────────────────────────────────────


@click.group()
def cli():
    """Manage Yoto CYO playlists as folders on disk."""
    pass


# ── auth ──────────────────────────────────────────────────────────────────────


@cli.command()
def auth():
    """Authenticate with Yoto (OAuth device code flow)."""
    try:
        run_device_code_flow()
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc


# ── sync ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Preview changes without executing")
def sync(path, dry_run):
    """Push local playlist state to Yoto."""
    if dry_run:
        results = sync_path(Path(path), dry_run=True)
        for result in results:
            icon_msg = f", {result.icons_uploaded} icons" if result.icons_uploaded else ""
            click.echo(f"[Dry run] Would upload {result.tracks_uploaded} tracks{icon_msg}")
            for error in result.errors:
                click.echo(f"Error: {error}", err=True)
        return

    from rich.progress import Progress

    with Progress() as progress:
        task_id = progress.add_task("Uploading tracks…", total=None)

        def on_track(filename: str):
            progress.advance(task_id)

        results = sync_path(Path(path), dry_run=False, on_track_done=on_track)

    for result in results:
        icon_msg = f", {result.icons_uploaded} icons" if result.icons_uploaded else ""
        click.echo(
            f"Synced card {result.card_id}: {result.tracks_uploaded} tracks uploaded{icon_msg}"
        )
        for error in result.errors:
            click.echo(f"Error: {error}", err=True)


# ── pull ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path_or_card_id", default=".")
@click.option("--dry-run", is_flag=True, help="Preview changes without executing")
@click.option("--all", "pull_all", is_flag=True, help="Pull all playlists into subdirectories of cwd")
def pull(path_or_card_id, dry_run, pull_all):
    """Pull remote playlist state to local."""
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


def _pull_one(folder: Path, card_id: str | None = None, dry_run: bool = False, progress=None) -> None:
    """Pull a single playlist with optional rich progress bar."""
    from rich.progress import Progress

    own_progress = progress is None
    if own_progress and not dry_run:
        progress = Progress()
        progress.start()

    task_id = None

    def on_track(title: str):
        nonlocal task_id
        if progress and task_id is not None:
            progress.advance(task_id)

    try:
        # Peek at track count for the progress bar
        if not dry_run and progress:
            api = YotoAPI()
            remote = api.get_content(card_id or _read_card_id(folder))
            chapters = remote.get("content", {}).get("chapters", [])
            total = sum(1 for ch in chapters for t in ch.get("tracks", []) if t.get("trackUrl", "").startswith("http"))
            label = folder.name or card_id or "Playlist"
            task_id = progress.add_task(label, total=total)

        result = pull_playlist(folder, card_id=card_id, dry_run=dry_run, on_track_done=on_track)
    finally:
        if own_progress and progress:
            progress.stop()

    if dry_run:
        click.echo(f"[Dry run] {result.card_id}")
    else:
        icon_msg = f", {result.icons_downloaded} icons" if result.icons_downloaded else ""
        click.echo(
            f"Pulled {result.card_id}: {result.tracks_downloaded} tracks{icon_msg}"
        )
    for error in result.errors:
        click.echo(f"Error: {error}", err=True)


def _read_card_id(folder: Path) -> str | None:
    card_id_path = folder / ".yoto-card-id"
    if card_id_path.exists():
        return card_id_path.read_text(encoding="utf-8").strip()
    return None


def _pull_all(dry_run: bool = False) -> None:
    """Pull every playlist on the account into a subdirectory of cwd."""
    from rich.progress import Progress

    api = YotoAPI()
    cards = api.get_my_content()

    if not cards:
        click.echo("No cards found.")
        return

    if dry_run:
        for card in cards:
            card_id = card.get("cardId", "")
            title = card.get("title", card_id)
            folder = Path(".") / title
            folder.mkdir(exist_ok=True)
            result = pull_playlist(folder, card_id=card_id, dry_run=True)
            click.echo(f"[Dry run] {title} ({card_id})")
        return

    with Progress() as progress:
        for card in cards:
            card_id = card.get("cardId", "")
            title = card.get("title", card_id)
            folder = Path(".") / title
            folder.mkdir(exist_ok=True)
            _pull_one(folder, card_id=card_id, progress=progress)


# ── status ────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def status(path):
    """Show diff between local and remote state."""
    folder = Path(path)
    if not (folder / "playlist.jsonl").exists() and not scan_audio_files(folder):
        raise click.ClickException("Not a playlist folder (no playlist.jsonl or audio files)")
    playlist = load_playlist(folder)

    remote_state = None
    if playlist.card_id:
        try:
            api = YotoAPI()
            remote_content = api.get_content(playlist.card_id)
            from yoto_lib.sync import _parse_remote_state
            remote_state = _parse_remote_state(remote_content)
        except Exception as exc:
            click.echo(f"Warning: could not fetch remote state: {exc}", err=True)

    diff = diff_playlists(playlist, remote_state)

    if not any([diff.new_tracks, diff.removed_tracks, diff.order_changed,
                diff.cover_changed, diff.metadata_changed]):
        click.echo("No changes.")
        return

    if diff.new_tracks:
        for t in diff.new_tracks:
            click.echo(f"  + {t}")
    if diff.removed_tracks:
        for t in diff.removed_tracks:
            click.echo(f"  - {t}")
    if diff.order_changed:
        click.echo("  ~ track order changed")
    if diff.cover_changed:
        click.echo("  ~ cover changed")
    if diff.metadata_changed:
        click.echo("  ~ metadata changed")


# ── reorder ───────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("playlist", default="playlist.jsonl", type=click.Path(exists=True))
def reorder(playlist):
    """Open playlist.jsonl in $EDITOR to reorder tracks."""
    playlist_path = Path(playlist)
    original = playlist_path.read_text(encoding="utf-8")

    edited = click.edit(original)

    if edited is None or edited == original:
        click.echo("No changes made.")
        return

    # Validate the edited content is valid JSONL
    import json
    filenames = []
    for i, line in enumerate(edited.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"Invalid JSON on line {i}: {exc}") from exc
        if not isinstance(value, str):
            raise click.ClickException(
                f"Line {i}: expected a JSON string, got {type(value).__name__}"
            )
        filenames.append(value)

    write_jsonl(playlist_path, filenames)
    click.echo(f"Saved {len(filenames)} tracks.")


# ── init ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path())
def init(path):
    """Scaffold a new playlist folder."""
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    jsonl_path = folder / "playlist.jsonl"
    if not jsonl_path.exists():
        write_jsonl(jsonl_path, [])
        click.echo(f"Created {jsonl_path}")
    else:
        click.echo(f"Already exists: {jsonl_path}")
    click.echo(f"Initialized playlist folder: {folder}")


# ── import ────────────────────────────────────────────────────────────────────


def _strip_track_number(stem: str) -> str:
    """Strip leading track number prefix from a filename stem.

    Handles: '01 Song', '01. Song', '01 - Song', '1-Song', '01_Song'
    """
    stripped = re.sub(r"^\d+[\s.\-_]+", "", stem)
    return stripped if stripped else stem


@cli.command(name="import")
@click.argument("source", type=click.Path(exists=True))
@click.option(
    "--output", "-o",
    default=None,
    type=click.Path(),
    help="Output folder (defaults to source folder)",
)
def import_cmd(source, output):
    """Bulk import: convert a folder of audio files into a playlist."""
    source_path = Path(source)
    output_path = Path(output) if output else source_path

    output_path.mkdir(parents=True, exist_ok=True)

    audio_files = scan_audio_files(source_path)
    if not audio_files:
        click.echo("No audio files found.")
        return

    filenames = []
    for audio in audio_files:
        clean_stem = _strip_track_number(audio.stem)
        mka_name = clean_stem + ".mka"
        mka_dest = output_path / mka_name
        if audio.suffix.lower() == ".mka" and source_path == output_path:
            # Already MKA in place — just record it
            filenames.append(mka_name)
        else:
            try:
                wrap_in_mka(audio, mka_dest)
                filenames.append(mka_name)
                click.echo(f"  Wrapped {audio.name} -> {mka_name}")
                if source_path == output_path:
                    audio.unlink()
            except Exception as exc:
                click.echo(f"  Error wrapping {audio.name}: {exc}", err=True)

    write_jsonl(output_path / "playlist.jsonl", filenames)
    click.echo(f"Imported {len(filenames)} tracks into {output_path}")


# ── list ──────────────────────────────────────────────────────────────────────


@cli.command(name="list")
def list_cmd():
    """Show all MYO cards on your Yoto account."""
    api = YotoAPI()
    cards = api.get_my_content()

    if not cards:
        click.echo("No cards found.")
        return

    # Print a simple table
    col_id = max(len(c.get("cardId", "")) for c in cards)
    col_id = max(col_id, 6)
    col_title = max(len(c.get("title", "")) for c in cards)
    col_title = max(col_title, 5)

    header = f"{'Card ID':<{col_id}}  {'Title':<{col_title}}  Tracks"
    click.echo(header)
    click.echo("-" * len(header))

    for card in cards:
        card_id = card.get("cardId", "")
        title = card.get("title", "")
        try:
            detail = api.get_content(card_id)
            chapters = detail.get("content", {}).get("chapters", [])
            num_tracks = len(chapters)
        except Exception:
            num_tracks = "?"
        click.echo(f"{card_id:<{col_id}}  {title:<{col_title}}  {num_tracks}")
