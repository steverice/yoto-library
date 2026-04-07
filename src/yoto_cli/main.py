"""Yoto CLI — manage CYO playlists as folders on disk."""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler
from pathlib import Path

WORKERS = int(os.environ.get("YOTO_WORKERS", "4"))

import click
from click.shell_completion import CompletionItem
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

logger = logging.getLogger(__name__)

from yoto_lib.auth import AuthError, run_device_code_flow
from yoto_lib.api import YotoAPI
from yoto_lib.description import generate_description
from yoto_lib.sync import sync_path
from yoto_lib.pull import pull_playlist
from yoto_lib.playlist import read_jsonl, write_jsonl, scan_audio_files, load_playlist, diff_playlists
from yoto_lib.mka import wrap_in_mka, remove_attachment, set_attachment, read_source_tags, write_tags, generate_source_patch, extract_album_art, read_tags
from yoto_lib.itunes import enrich_from_itunes
from yoto_lib.sources import resolve_weblocs
from yoto_lib.costs import get_tracker, reset_tracker


def _print_cost_summary():
    from yoto_cli.progress import _console
    tracker = get_tracker()
    if not tracker.has_records():
        return
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
    Heuristic: treat as card_id if it is a short (≤10 chars) alphanumeric
    string that does NOT exist as a path on disk.
    """
    return (
        bool(re.fullmatch(r"[A-Za-z0-9]{1,10}", value))
        and not Path(value).exists()
    )


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


# ── auth ──────────────────────────────────────────────────────────────────────


@cli.command()
def auth():
    """Authenticate with Yoto (OAuth device code flow)."""
    logger.debug("command: auth")
    try:
        run_device_code_flow()
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc


# ── sync ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Preview changes without executing")
@click.option("--no-trim", is_flag=True, help="Skip silence trimming on YouTube downloads")
def sync(path, dry_run, no_trim):
    """Push local playlist state to Yoto."""
    logger.debug("command: sync path=%s dry_run=%s no_trim=%s", path, dry_run, no_trim)
    trim = not no_trim
    reset_tracker()
    from yoto_cli.progress import _console, error as _error
    if dry_run:
        results = sync_path(Path(path), dry_run=True, trim=trim)
        for result in results:
            icon_msg = f", {result.icons_uploaded} icons" if result.icons_uploaded else ""
            _console.print(f"[Dry run] Would upload {result.tracks_uploaded} tracks{icon_msg}")
            for err in result.errors:
                _error(err)
        return

    from yoto_lib.playlist import load_playlist as _load
    from yoto_cli.progress import make_progress
    playlist = _load(Path(path))
    total = len(playlist.track_files) * 2 + 2

    with make_progress() as progress:
        task = progress.add_task(playlist.title, total=total, status="starting")
        upload_tasks: dict[str, int] = {}  # filename -> inner task id

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
            Path(path), dry_run=False, trim=trim, log=log,
            on_upload_start=on_upload_start, on_upload_done=on_upload_done,
        )
        progress.update(task, completed=total)

    from yoto_cli.progress import success as _success, error as _error
    for result in results:
        icon_msg = f", {result.icons_uploaded} icons" if result.icons_uploaded else ""
        _success(f"card {result.card_id}: {result.tracks_uploaded} tracks{icon_msg}")
        for err in result.errors:
            _error(err)
    _print_cost_summary()


# ── download ─────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True), shell_complete=_complete_weblocs)
@click.option("--no-trim", is_flag=True, help="Skip silence trimming on YouTube downloads")
def download(path, no_trim):
    """Download audio from .webloc URLs in a playlist folder."""
    logger.debug("command: download path=%s no_trim=%s", path, no_trim)
    trim = not no_trim
    folder = Path(path)

    webloc_count = len(list(folder.glob("*.webloc")))
    if sys.stderr.isatty() and webloc_count > 0:
        from yoto_cli.progress import make_progress
        with make_progress() as progress:
            task = progress.add_task(folder.name, total=webloc_count, status="")
            inner_tasks: dict[str, int] = {}

            def on_track_start(name: str) -> None:
                inner_task = progress.add_task(name, total=100, status="")
                inner_tasks[name] = inner_task

            def on_download_progress(name: str, pct: float, downloaded: int, total: int | None, speed: str) -> None:
                inner_task = inner_tasks.get(name)
                if inner_task is not None:
                    status = speed if speed else ""
                    progress.update(inner_task, completed=pct, status=status)

            def on_track(name: str) -> None:
                progress.update(task, advance=1, status=name)
                inner_task = inner_tasks.pop(name.split(".mka")[0].split("/")[-1] if ".mka" in name else name, None)
                # Also try by stem (mka name != webloc stem)
                if inner_task is None:
                    for key, tid in list(inner_tasks.items()):
                        inner_tasks.pop(key)
                        inner_task = tid
                        break
                if inner_task is not None:
                    progress.remove_task(inner_task)

            created = resolve_weblocs(
                folder, trim=trim,
                on_track_done=on_track,
                on_track_start=on_track_start,
                on_download_progress=on_download_progress,
            )
    else:
        created = resolve_weblocs(folder, trim=trim)

    from yoto_cli.progress import _console, success as _success
    if not created:
        _console.print("[dim]No .webloc files resolved.[/dim]")
        return

    for mka_path in created:
        _success(f"Downloaded: {mka_path.name}")
    _success(f"Downloaded {len(created)} tracks.")


# ── pull ──────────────────────────────────────────────────────────────────────


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
                # total=None → indeterminate until we get content-length
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


def _read_card_id(folder: Path) -> str | None:
    card_id_path = folder / ".yoto-card-id"
    if card_id_path.exists():
        return card_id_path.read_text(encoding="utf-8").strip()
    return None


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


# ── status ────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def status(path):
    """Show diff between local and remote state."""
    logger.debug("command: status path=%s", path)
    folder = Path(path)
    if not (folder / "playlist.jsonl").exists() and not scan_audio_files(folder):
        raise click.ClickException("Not a playlist folder (no playlist.jsonl or audio files)")
    playlist = load_playlist(folder)

    from yoto_cli.progress import _console, warning as _warning
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

    if not any([diff.new_tracks, diff.removed_tracks, diff.order_changed,
                diff.cover_changed, diff.metadata_changed]):
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


# ── reorder ───────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("playlist", default="playlist.jsonl", type=click.Path(exists=True))
def reorder(playlist):
    """Open playlist.jsonl in $EDITOR to reorder tracks."""
    logger.debug("command: reorder playlist=%s", playlist)
    playlist_path = Path(playlist)
    original = playlist_path.read_text(encoding="utf-8")

    edited = click.edit(original)

    from yoto_cli.progress import _console, success as _success
    if edited is None or edited == original:
        _console.print("[dim]No changes made.[/dim]")
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
    _success(f"Saved {len(filenames)} tracks.")


# ── init ──────────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path())
def init(path):
    """Scaffold a new playlist folder."""
    logger.debug("command: init path=%s", path)
    folder = Path(path)
    from yoto_cli.progress import success as _success, warning as _warning
    folder.mkdir(parents=True, exist_ok=True)
    jsonl_path = folder / "playlist.jsonl"
    if not jsonl_path.exists():
        write_jsonl(jsonl_path, [])
        _success(f"Created {jsonl_path}")
    else:
        _warning(f"Already exists: {jsonl_path}")
    _success(f"Initialized playlist folder: {folder}")


# ── import ────────────────────────────────────────────────────────────────────


def _strip_track_number(stem: str) -> str:
    """Strip leading track number prefix from a filename stem.

    Handles: '01 Song', '01. Song', '01 - Song', '1-Song', '01_Song'
    """
    stripped = re.sub(r"^\d+[\s.\-_]+", "", stem)
    return stripped if stripped else stem


@cli.command(name="import")
@click.argument("source", type=click.Path(exists=True), shell_complete=_complete_unimported_dirs)
@click.option(
    "--output", "-o",
    default=None,
    type=click.Path(),
    help="Output folder (defaults to source folder)",
)
def import_cmd(source, output):
    """Bulk import: convert a folder of audio files into a playlist."""
    logger.debug("command: import source=%s output=%s", source, output)
    source_path = Path(source)
    output_path = Path(output) if output else source_path

    output_path.mkdir(parents=True, exist_ok=True)
    reset_tracker()

    from yoto_cli.progress import _console, success as _success, error as _error
    audio_files = scan_audio_files(source_path)
    if not audio_files:
        _console.print("[dim]No audio files found.[/dim]")
        return

    album_cache: dict = {}
    album_cache_lock = threading.Lock()
    # results_by_index preserves original order: index -> mka_name or None
    results_by_index: dict[int, str | None] = {}

    from contextlib import nullcontext
    from yoto_cli.progress import make_progress
    progress_ctx = make_progress() if sys.stderr.isatty() else nullcontext()
    with progress_ctx as progress:
        task = progress.add_task(source_path.name, total=len(audio_files), status="") if progress else None

        def _import_one(idx: int, audio: Path) -> str | None:
            """Process one audio file. Returns mka_name on success, None on failure."""
            clean_stem = _strip_track_number(audio.stem)
            mka_name = clean_stem + ".mka"
            mka_dest = output_path / mka_name

            if audio.suffix.lower() == ".mka" and source_path == output_path:
                # Already MKA in place — just record it
                if progress and task is not None:
                    progress.update(task, advance=1, status=audio.name)
                return mka_name

            inner_task = progress.add_task(audio.name, total=4, status="wrapping") if progress else None
            try:
                wrap_in_mka(audio, mka_dest)
                if progress and inner_task is not None:
                    progress.update(inner_task, advance=1, status="metadata")
                # Copy metadata from source file to MKA
                source_tags = read_source_tags(audio)
                source_tags["source_format"] = audio.suffix.lstrip(".").lower()
                write_tags(mka_dest, source_tags)
                if progress and inner_task is not None:
                    progress.update(inner_task, advance=1, status="fetching art")
                # Fetch album art from iTunes (serialized to avoid duplicate API calls)
                with album_cache_lock:
                    enrich_from_itunes(mka_dest, source_tags, album_cache)
                if progress and inner_task is not None:
                    progress.update(inner_task, advance=1, status="patching")
                # Generate bsdiff patch for byte-perfect export
                generate_source_patch(audio, mka_dest)
                if progress and inner_task is not None:
                    progress.update(inner_task, advance=1)
                    progress.remove_task(inner_task)
                if source_path == output_path:
                    audio.unlink()
                if progress and task is not None:
                    progress.update(task, advance=1, status=audio.name)
                return mka_name
            except Exception as exc:
                if progress and inner_task is not None:
                    progress.remove_task(inner_task)
                _pcon = progress.console.print if progress else _console.print
                _pcon(f"  [red]✗[/red] Error wrapping {audio.name}: {exc}")
                if progress and task is not None:
                    progress.update(task, advance=1, status=audio.name)
                return None

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            future_to_idx = {
                executor.submit(_import_one, idx, audio): idx
                for idx, audio in enumerate(audio_files)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results_by_index[idx] = future.result()
                except Exception as exc:
                    _console.print(f"  [red]✗[/red] Unexpected error: {exc}")
                    results_by_index[idx] = None

    filenames = [
        name for i in range(len(audio_files))
        if (name := results_by_index.get(i)) is not None
    ]

    write_jsonl(output_path / "playlist.jsonl", filenames)
    _success(f"Imported {len(filenames)} tracks into {output_path}")

    # Generate description from track metadata
    from rich.prompt import Prompt
    playlist = load_playlist(output_path)

    # Enrich tracks that are missing album art (e.g. re-import of existing MKAs)
    if not playlist.has_cover:
        for filename in playlist.track_files:
            track_path = output_path / filename
            if extract_album_art(track_path) is None:
                tags = read_tags(track_path)
                enrich_from_itunes(track_path, tags, album_cache)

    # Generate description from track metadata
    generate_description(
        playlist,
        log=lambda msg: _console.print(msg),
        ask_user=lambda q: Prompt.ask(q, console=_console),
    )
    _print_cost_summary()


# ── export ───────────────────────────────────────────────────────────────


@cli.command()
@click.argument("playlist", type=click.Path(exists=True), shell_complete=_complete_dirs)
@click.option(
    "--output", "-o",
    default=None,
    type=click.Path(),
    help="Output folder (defaults to <playlist>-exported/)",
)
def export(playlist, output):
    """Export MKA tracks back to their original audio format."""
    from yoto_lib.mka import extract_audio, apply_source_patch

    logger.debug("command: export playlist=%s output=%s", playlist, output)
    playlist_path = Path(playlist)
    output_path = Path(output) if output else playlist_path.parent / f"{playlist_path.name}-exported"
    output_path.mkdir(parents=True, exist_ok=True)

    from yoto_cli.progress import _console, success as _success
    mka_files = sorted(playlist_path.glob("*.mka"))
    if not mka_files:
        _console.print("[dim]No .mka files found.[/dim]")
        return

    import shutil
    import tempfile
    from yoto_lib.mka import get_attachment, PATCH_ATTACHMENT_NAME
    from contextlib import nullcontext
    from yoto_cli.progress import make_progress

    progress_ctx = make_progress() if sys.stderr.isatty() else nullcontext()
    with progress_ctx as progress:
        task = progress.add_task(playlist_path.name, total=len(mka_files), status="") if progress else None
        _pcon = progress.console.print if progress else _console.print

        def _export_one(mka: Path) -> None:
            inner_task = progress.add_task(mka.name, total=2, status="extracting") if progress else None
            try:
                has_patch = get_attachment(mka, PATCH_ATTACHMENT_NAME) is not None

                if has_patch:
                    # Extract to temp dir, then apply patch to final location
                    with tempfile.TemporaryDirectory(prefix="yoto-export-") as tmpdir:
                        extracted = extract_audio(mka, Path(tmpdir))
                        if progress and inner_task is not None:
                            progress.update(inner_task, advance=1, status="applying patch")
                        final_path = output_path / (mka.stem + extracted.suffix)
                        if apply_source_patch(extracted, mka, final_path):
                            _pcon(f"  {mka.name} -> {final_path.name} (byte-perfect)")
                        else:
                            # Patch failed — copy the extraction as fallback
                            shutil.copy2(extracted, final_path)
                            _pcon(f"  {mka.name} -> {final_path.name}")
                else:
                    # No patch — extract directly to output
                    extracted = extract_audio(mka, output_path)
                    _pcon(f"  {mka.name} -> {extracted.name}")
                if progress and inner_task is not None:
                    progress.update(inner_task, advance=1)
                    progress.remove_task(inner_task)
            except Exception as exc:
                if progress and inner_task is not None:
                    progress.remove_task(inner_task)
                _pcon(f"  [red]✗[/red] Error exporting {mka.name}: {exc}")
            if progress and task is not None:
                progress.update(task, advance=1, status=mka.name)

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = [executor.submit(_export_one, mka) for mka in mka_files]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    _pcon(f"  [red]✗[/red] Unexpected export error: {exc}")

    _success(f"Exported {len(mka_files)} tracks to {output_path}")


# ── select-icon ──────────────────────────────────────────────────────────


@cli.command(name="select-icon")
@click.argument("tracks", nargs=-1, required=True, type=click.Path(exists=True), shell_complete=_complete_mka_without_icon)
def select_icon(tracks):
    """Generate 3 icon options per track, show best Yoto match, and attach the chosen one."""
    logger.debug("command: select-icon tracks=%s", tracks)
    import io
    import tempfile
    from PIL import Image
    from yoto_lib.icons import generate_retrodiffusion_icons, download_icon, set_macos_file_icon
    from yoto_lib.mka import get_attachment
    from yoto_lib.icon_catalog import get_catalog
    from yoto_lib.icon_llm import match_icon_llm, compare_icons_llm, describe_icons_llm, log_icon_feedback

    from yoto_cli.progress import make_progress, _console, success as _success, warning as _warning
    from rich.rule import Rule
    from rich.prompt import Prompt
    reset_tracker()

    # Shared resources — loaded once
    api = YotoAPI()
    catalog = get_catalog(api)

    for i, track in enumerate(tracks):
        track_path = Path(track)
        title = track_path.stem
        album_desc = None
        desc_path = track_path.resolve().parent / "description.txt"
        if desc_path.exists():
            album_desc = desc_path.read_text(encoding="utf-8")

        if len(tracks) > 1:
            _console.print(Rule(title=f"{track_path.name} ({i + 1}/{len(tracks)})"))

        # Check for existing icon
        existing_img: "Image.Image | None" = None
        try:
            existing_bytes = get_attachment(track_path, "icon")
            if existing_bytes:
                existing_img = Image.open(io.BytesIO(existing_bytes)).convert("RGBA").resize((16, 16), Image.NEAREST)
        except Exception:
            pass

        yoto_media_id, yoto_confidence = None, 0.0
        yoto_img: "Image.Image | None" = None
        yoto_title: str | None = None
        yoto_bytes: bytes | None = None

        with make_progress() as progress:
            task = progress.add_task(title, total=7, status="matching Yoto icon")

            inner = progress.add_task("Claude Haiku", total=None, status="")
            yoto_media_id, yoto_confidence = match_icon_llm(title, catalog)
            progress.remove_task(inner)

            if yoto_media_id:
                yoto_bytes = download_icon(yoto_media_id)
                if yoto_bytes:
                    yoto_img = Image.open(io.BytesIO(yoto_bytes)).convert("RGBA").resize((16, 16), Image.NEAREST)
                    for icon in catalog:
                        if icon.get("mediaId") == yoto_media_id:
                            yoto_title = icon.get("title", "") or icon.get("name", "")
                            break

            progress.update(task, advance=1, status="describing icons")
            tmpdir = Path(tempfile.mkdtemp(prefix="yoto-icon-"))
            skipped = False

            inner = progress.add_task("Claude Haiku", total=None, status="")
            descriptions = describe_icons_llm(title, album_description=album_desc)
            progress.remove_task(inner)
            if not descriptions:
                descriptions = [title, title, title]  # fallback to raw title

            progress.update(task, advance=1, status="generating icon 1/3")

            icon_tasks: dict[int, int] = {}

            def on_icon_start(i: int, desc: str) -> None:
                icon_tasks[i] = progress.add_task(f"Icon {i + 1}: {desc}", total=None, status="")

            def on_icon_done(i: int) -> None:
                if i in icon_tasks:
                    progress.remove_task(icon_tasks.pop(i))

            def on_gen_progress(done_n: int) -> None:
                if done_n < 3:
                    progress.update(task, advance=1, status=f"generating icon {done_n + 1}/3")
                else:
                    progress.update(task, advance=1, status="evaluating icons")

            batch = generate_retrodiffusion_icons(
                descriptions,
                on_progress=on_gen_progress,
                on_icon_start=on_icon_start,
                on_icon_done=on_icon_done,
            )
            if not batch:
                progress.console.print(f"[red]✗[/red] Icon generation failed for {track_path.name}")
                skipped = True
            else:
                raw_bytes_list: list[bytes] = [rb for rb, _ in batch]
                inner = progress.add_task("Claude Sonnet", total=None, status="")
                winner, scores = compare_icons_llm(
                    title, raw_bytes_list,
                    yoto_icon=yoto_bytes if yoto_img is not None else None,
                    descriptions=descriptions,
                    album_description=album_desc,
                )
                progress.remove_task(inner)
                progress.update(task, advance=1)
        # progress bar closed — interactive prompt starts below

        if skipped:
            tmpdir.rmdir()
            continue

        while True:
            icons_16: list[Image.Image] = [processed for _, processed in batch]
            images_to_show: list[Image.Image] = list(icons_16)
            labels_to_show: list[str] = [
                f"[{i + 1}] {descriptions[i] if i < len(descriptions) else 'AI'}"
                for i in range(len(icons_16))
            ]

            next_idx = len(images_to_show) + 1

            if yoto_img is not None:
                images_to_show.append(yoto_img)
                labels_to_show.append(f"[{next_idx}] \"{yoto_title}\"")
                yoto_choice = next_idx
                next_idx += 1
            else:
                yoto_choice = None

            if existing_img is not None:
                images_to_show.append(existing_img)
                labels_to_show.append(f"[{next_idx}] current")
                existing_choice = next_idx
                next_idx += 1
            else:
                existing_choice = None

            max_choice = next_idx - 1
            prompt_text = f"Pick an icon (1-{max_choice}, or 'r' to regenerate)"

            # Build score labels (existing icon gets no score)
            from yoto_cli.progress import render_icon_panels
            score_labels = []
            for j in range(len(images_to_show)):
                if (j + 1) == existing_choice:
                    score_labels.append("")
                else:
                    score = f"{scores[j]:.1f}" if j < len(scores) else "?"
                    marker = " ★" if (j + 1) == winner else ""
                    score_labels.append(f"score: {score}{marker}")

            _console.print(render_icon_panels(images_to_show, labels_to_show, score_labels, winner))

            default_choice = str(winner) if 1 <= winner <= max_choice else "1"
            raw = Prompt.ask(prompt_text, default=default_choice, console=_console)
            if raw.lower() == "r":
                with make_progress() as progress:
                    task = progress.add_task(title, total=6, status="describing icons")

                    inner = progress.add_task("Claude Haiku", total=None, status="")
                    descriptions = describe_icons_llm(title, album_description=album_desc)
                    progress.remove_task(inner)
                    if not descriptions:
                        descriptions = [title, title, title]
                    progress.update(task, advance=1, status="generating icon 1/3")

                    regen_icon_tasks: dict[int, int] = {}

                    def on_icon_start_r(i: int, desc: str) -> None:
                        regen_icon_tasks[i] = progress.add_task(f"Icon {i + 1}: {desc}", total=None, status="")

                    def on_icon_done_r(i: int) -> None:
                        if i in regen_icon_tasks:
                            progress.remove_task(regen_icon_tasks.pop(i))

                    def on_gen_progress_r(done_n: int) -> None:
                        if done_n < 3:
                            progress.update(task, advance=1, status=f"generating icon {done_n + 1}/3")
                        else:
                            progress.update(task, advance=1, status="evaluating icons")

                    batch = generate_retrodiffusion_icons(
                        descriptions,
                        on_progress=on_gen_progress_r,
                        on_icon_start=on_icon_start_r,
                        on_icon_done=on_icon_done_r,
                    )
                    if not batch:
                        progress.console.print(f"[red]✗[/red] Icon generation failed for {track_path.name}")
                        skipped = True
                    else:
                        raw_bytes_list = [rb for rb, _ in batch]
                        inner = progress.add_task("Claude Sonnet", total=None, status="")
                        winner, scores = compare_icons_llm(
                            title, raw_bytes_list,
                            yoto_icon=yoto_bytes if yoto_img is not None else None,
                            descriptions=descriptions,
                            album_description=album_desc,
                        )
                        progress.remove_task(inner)
                        progress.update(task, advance=1)
                if skipped:
                    break
                continue

            try:
                choice = int(raw)
                if not 1 <= choice <= max_choice:
                    raise ValueError
            except ValueError:
                _warning("Invalid choice.")
                continue

            if choice == existing_choice:
                _console.print(f"[dim]Keeping current icon for {track_path.name}[/dim]")
                skipped = True
                break
            elif choice == yoto_choice:
                chosen = yoto_img
            else:
                chosen = icons_16[choice - 1]

            # Log feedback for tuning
            log_icon_feedback(
                track_title=title,
                llm_winner=winner,
                llm_scores=scores,
                user_choice=choice,
                descriptions=descriptions,
                album=track_path.resolve().parent.name,
                chose_yoto=(choice == yoto_choice),
            )
            break

        if skipped:
            tmpdir.rmdir()
            continue

        buf = io.BytesIO()
        chosen.save(buf, format="PNG")
        icon_bytes = buf.getvalue()

        icon_tmp = tmpdir / "chosen_icon.png"
        icon_tmp.write_bytes(icon_bytes)
        set_attachment(track_path, icon_tmp, name="icon", mime_type="image/png")

        set_macos_file_icon(track_path, chosen)
        _success(f"Attached icon to {track_path.name}")

        icon_tmp.unlink(missing_ok=True)
        tmpdir.rmdir()

    _print_cost_summary()


# ── reset-icon ───────────────────────────────────────────────────────────


@cli.command(name="reset-icon")
@click.argument("tracks", nargs=-1, required=True, type=click.Path(exists=True), shell_complete=_complete_mka_with_icon)
def reset_icon(tracks):
    """Remove the icon from one or more MKA tracks so sync regenerates them."""
    logger.debug("command: reset-icon tracks=%s", tracks)
    from yoto_lib.icons import clear_macos_file_icon

    from yoto_cli.progress import success as _success, error as _error
    for track in tracks:
        path = Path(track)
        try:
            remove_attachment(path, "icon")
            clear_macos_file_icon(path)
            _success(f"Cleared icon: {path.name}")
        except Exception as exc:
            _error(f"Error ({path.name}): {exc}")


# ── cover ────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True), shell_complete=_complete_dirs)
@click.option("--force", is_flag=True, help="Regenerate even if cover.png exists")
@click.option("--backup", is_flag=True, help="Like --force, but rename existing cover.png first")
def cover(path, force, backup):
    """Generate cover art for a playlist folder."""
    if force and backup:
        raise click.UsageError("--force and --backup are mutually exclusive")
    logger.debug("command: cover path=%s force=%s backup=%s", path, force, backup)
    reset_tracker()
    from yoto_lib.cover import build_cover_prompt, resize_cover, try_shared_album_art, add_title_to_illustration
    from yoto_lib.image_providers import get_provider
    from yoto_lib import mka
    import tempfile

    from yoto_cli.progress import _console as rich_console

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

    from yoto_cli.progress import make_progress, success as _success
    from yoto_lib.cover import RECOMPOSE_MAX_ATTEMPTS

    cover_name = playlist.title or folder.name
    title_steps = 1 if playlist.title else 0
    # Worst case: all recompose attempts + generate + optional title + save
    total_steps = RECOMPOSE_MAX_ATTEMPTS + 1 + title_steps + 1

    with make_progress() as progress:
        task = progress.add_task(cover_name, total=total_steps, status="checking album art")

        # Tracks the current inner task for nested progress
        _inner_task: list[int | None] = [None]

        def _cover_log(msg: str) -> None:
            if msg.startswith("WARNING:"):
                progress.console.print(f"[yellow]⚠ {msg}[/yellow]")
            else:
                progress.update(task, status=msg)

        def _cover_step() -> None:
            progress.update(task, advance=1)

        def _cover_inner(status: "str | None", step: "int | None", total: "int | None") -> None:
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
                progress.update(_inner_task[0], description=status, completed=step if step is not None else 0, total=total)

        # Try reusing shared album art first
        if try_shared_album_art(playlist, log=_cover_log, on_step=_cover_step, on_inner=_cover_inner):
            progress.update(task, completed=total_steps)
            progress.stop()
            _success(f"Reused album art as cover: {cover_path}")
            _print_cost_summary()
            return

        # No shared art — generate from scratch
        progress.update(task, completed=RECOMPOSE_MAX_ATTEMPTS, status="generating cover art")

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

        prompt = build_cover_prompt(playlist.description, track_titles, artists, playlist.title)

        provider = get_provider()
        # Request 1024×1536 — maps exactly to that OpenAI size (~0.667),
        # only ~28px cropped per side to reach 638:1011 (~0.631) target.
        inner = progress.add_task("Generating", total=None, status="")
        image_bytes = provider.generate(prompt, 1024, 1536)
        progress.remove_task(inner)
        progress.update(task, advance=1, status="generated cover art")

        if playlist.title:
            progress.update(task, advance=1, status="adding title")
            inner = progress.add_task("Adding title", total=None, status="")
            image_bytes = add_title_to_illustration(image_bytes, playlist.title, 1024, 1536)
            progress.remove_task(inner)
            progress.update(task, status="title added")

        progress.update(task, advance=1, status="saving")
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


# ── completions ──────────────────────────────────────────────────────────────


@cli.command()
@click.argument("shell", required=False, default=None, type=click.Choice(["zsh", "bash", "fish"]))
def completions(shell):
    """Install context-aware shell completions."""
    logger.debug("command: completions shell=%s", shell)
    if shell is None:
        parent_shell = Path(os.environ.get("SHELL", "")).name
        shell = parent_shell if parent_shell in ("zsh", "bash", "fish") else None
        if shell is None:
            raise click.ClickException("Could not detect shell. Pass zsh, bash, or fish.")

    env_var = f"_YOTO_COMPLETE={shell}_source"
    marker = "# yoto shell completions"

    if shell == "zsh":
        line = f'eval "$({env_var} yoto)"'
        config = Path.home() / ".zshrc"
    elif shell == "bash":
        line = f'eval "$({env_var} yoto)"'
        config = Path.home() / ".bashrc"
    else:
        line = f"eval ({env_var} yoto)"
        config = Path.home() / ".config" / "fish" / "completions" / "yoto.fish"

    from yoto_cli.progress import _console, success as _success
    # Check if already installed
    if config.exists() and marker in config.read_text(encoding="utf-8"):
        _console.print(f"[dim]Completions already installed in {config}[/dim]")
        return

    # Append to config
    config.parent.mkdir(parents=True, exist_ok=True)
    with open(config, "a", encoding="utf-8") as f:
        f.write(f"\n{marker}\n{line}\n")

    _success(f"Installed completions in {config}")
    _console.print(f"[dim]Run this to activate now:  source {config}[/dim]")


# ── list ──────────────────────────────────────────────────────────────────────


@cli.command(name="list")
def list_cmd():
    """Show all MYO cards on your Yoto account."""
    logger.debug("command: list")
    api = YotoAPI()
    cards = api.get_my_content()

    from yoto_cli.progress import _console
    from rich.table import Table
    if not cards:
        _console.print("[dim]No cards found.[/dim]")
        return

    table = Table()
    table.add_column("Card ID", style="dim")
    table.add_column("Title")
    table.add_column("Tracks", justify="right")

    for card in cards:
        card_id = card.get("cardId", "")
        title = card.get("title", "")
        try:
            detail = api.get_content(card_id)
            chapters = detail.get("content", {}).get("chapters", [])
            num_tracks = str(len(chapters))
        except Exception:
            num_tracks = "?"
        table.add_row(card_id, title, num_tracks)

    _console.print(table)
