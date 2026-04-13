"""import and download commands."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from rich.progress import TaskID

from yoto_cli.main import _complete_unimported_dirs, _complete_weblocs, _print_cost_summary, cli
from yoto_lib.billing.costs import reset_tracker
from yoto_lib.config import WORKERS
from yoto_lib.covers.itunes import enrich_from_itunes
from yoto_lib.description import generate_description
from yoto_lib.lyrics import get_lyrics
from yoto_lib.mka import extract_album_art, generate_source_patch, read_source_tags, read_tags, wrap_in_mka, write_tags
from yoto_lib.playlist import load_playlist, scan_audio_files, write_jsonl
from yoto_lib.track_sources import resolve_weblocs

logger = logging.getLogger(__name__)


def _strip_track_number(stem: str) -> str:
    """Strip leading track number prefix from a filename stem.

    Handles: '01 Song', '01. Song', '01 - Song', '1-Song', '01_Song'
    """
    stripped = re.sub(r"^\d+[\s.\-_]+", "", stem)
    return stripped if stripped else stem


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True), shell_complete=_complete_weblocs)
@click.option("--no-trim", is_flag=True, help="Skip silence trimming on YouTube downloads")
def download(path: str, no_trim: bool) -> None:
    """Download audio from .webloc URLs in a playlist folder."""
    logger.debug("command: download path=%s no_trim=%s", path, no_trim)
    trim = not no_trim
    folder = Path(path)

    # Support passing a single .webloc file directly
    if folder.is_file() and folder.suffix.lower() == ".webloc":
        webloc_files: list[Path] | None = [folder]
        folder = folder.parent
    else:
        webloc_files = None

    webloc_count = len(webloc_files) if webloc_files is not None else len(list(folder.glob("*.webloc")))
    if sys.stderr.isatty() and webloc_count > 0:
        from yoto_cli.progress import make_progress

        with make_progress() as progress:
            task = progress.add_task(folder.name, total=webloc_count, status="")
            inner_tasks: dict[str, TaskID] = {}

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
                folder,
                trim=trim,
                on_track_done=on_track,
                on_track_start=on_track_start,
                on_download_progress=on_download_progress,
                webloc_files=webloc_files,
            )
    else:
        created = resolve_weblocs(folder, trim=trim, webloc_files=webloc_files)

    from yoto_cli.progress import _console
    from yoto_cli.progress import success as _success

    if not created:
        _console.print("[dim]No .webloc files resolved.[/dim]")
        return

    for mka_path in created:
        _success(f"Downloaded: {mka_path.name}")
    _success(f"Downloaded {len(created)} tracks.")


# TODO: The import command contains ~100 lines of orchestration logic
# (wrap_in_mka, metadata tagging, iTunes enrichment, lyrics fetching, patch
# generation) that could be extracted to a library function in
# yoto_lib/import_.py, similar to the select-icon extraction into
# yoto_lib/icons/select.py.
@cli.command(name="import")
@click.argument("source", type=click.Path(exists=True), shell_complete=_complete_unimported_dirs)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(),
    help="Output folder (defaults to source folder)",
)
def import_cmd(source: str, output: str | None) -> None:
    """Bulk import: convert a folder of audio files into a playlist."""
    logger.debug("command: import source=%s output=%s", source, output)
    source_path = Path(source)
    output_path = Path(output) if output else source_path

    output_path.mkdir(parents=True, exist_ok=True)
    reset_tracker()

    from yoto_cli.progress import _console
    from yoto_cli.progress import success as _success

    # Resolve .webloc files into .mka tracks before scanning for audio
    webloc_count = len(list(source_path.glob("*.webloc")))
    if webloc_count > 0:
        if sys.stderr.isatty():
            from yoto_cli.progress import make_progress

            with make_progress() as dl_progress:
                dl_task = dl_progress.add_task("Downloading", total=webloc_count, status="")
                inner_tasks: dict[str, TaskID] = {}

                def on_track_start(name: str) -> None:
                    inner_tasks[name] = dl_progress.add_task(name, total=100, status="")

                def on_download_progress(name: str, pct: float, downloaded: int, total: int | None, speed: str) -> None:
                    inner_task = inner_tasks.get(name)
                    if inner_task is not None:
                        dl_progress.update(inner_task, completed=pct, status=speed or "")

                def on_track_done(name: str) -> None:
                    dl_progress.update(dl_task, advance=1, status=name)
                    for key, tid in list(inner_tasks.items()):
                        inner_tasks.pop(key)
                        dl_progress.remove_task(tid)
                        break

                downloaded = resolve_weblocs(
                    source_path,
                    on_track_start=on_track_start,
                    on_download_progress=on_download_progress,
                    on_track_done=on_track_done,
                )
        else:
            downloaded = resolve_weblocs(source_path)

        for mka_path in downloaded:
            _console.print(f"  Downloaded: {mka_path.name}")

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
                # Already MKA in place -- just record it
                if progress and task is not None:
                    progress.update(task, advance=1, status=audio.name)
                return mka_name

            inner_task = progress.add_task(audio.name, total=5, status="wrapping") if progress else None
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
                    progress.update(inner_task, advance=1, status="lyrics")
                # Fetch lyrics from source tags or LRCLIB
                lyrics_text, lyrics_source = get_lyrics(source_tags)
                if lyrics_text:
                    write_tags(mka_dest, {"lyrics": lyrics_text})
                    if progress:
                        progress.console.print(f"  [dim]Lyrics: found in {lyrics_source}[/dim]")
                else:
                    if progress:
                        progress.console.print("  [dim]Lyrics: not found[/dim]")
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
            except (subprocess.CalledProcessError, OSError) as exc:
                if progress and inner_task is not None:
                    progress.remove_task(inner_task)
                _pcon = progress.console.print if progress else _console.print
                _pcon(f"  [red]x[/red] Error wrapping {audio.name}: {exc}")
                if progress and task is not None:
                    progress.update(task, advance=1, status=audio.name)
                return None

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            future_to_idx = {executor.submit(_import_one, idx, audio): idx for idx, audio in enumerate(audio_files)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results_by_index[idx] = future.result()
                except (subprocess.CalledProcessError, OSError) as exc:
                    _console.print(f"  [red]x[/red] Unexpected error: {exc}")
                    results_by_index[idx] = None

    filenames = [name for i in range(len(audio_files)) if (name := results_by_index.get(i)) is not None]

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
