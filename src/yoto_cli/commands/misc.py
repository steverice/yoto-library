"""Smaller commands: auth, reorder, init, export, list, completions."""

from __future__ import annotations

import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

from yoto_cli.main import _complete_dirs, cli
from yoto_lib.config import WORKERS
from yoto_lib.playlist import write_jsonl
from yoto_lib.yoto.api import YotoAPI
from yoto_lib.yoto.auth import AuthError, run_device_code_flow

logger = logging.getLogger(__name__)


@cli.command()
def auth():
    """Authenticate with Yoto (OAuth device code flow)."""
    logger.debug("command: auth")
    try:
        run_device_code_flow()
    except AuthError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.argument("playlist", default="playlist.jsonl", type=click.Path(exists=True))
def reorder(playlist):
    """Open playlist.jsonl in $EDITOR to reorder tracks."""
    logger.debug("command: reorder playlist=%s", playlist)
    playlist_path = Path(playlist)
    original = playlist_path.read_text(encoding="utf-8")

    edited = click.edit(original)

    from yoto_cli.progress import _console
    from yoto_cli.progress import success as _success

    if edited is None or edited == original:
        _console.print("[dim]No changes made.[/dim]")
        return

    # Validate the edited content is valid JSONL
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
            raise click.ClickException(f"Line {i}: expected a JSON string, got {type(value).__name__}")
        filenames.append(value)

    write_jsonl(playlist_path, filenames)
    _success(f"Saved {len(filenames)} tracks.")


@cli.command()
@click.argument("path", default=".", type=click.Path())
def init(path):
    """Scaffold a new playlist folder."""
    logger.debug("command: init path=%s", path)
    folder = Path(path)
    from yoto_cli.progress import success as _success
    from yoto_cli.progress import warning as _warning

    folder.mkdir(parents=True, exist_ok=True)
    jsonl_path = folder / "playlist.jsonl"
    if not jsonl_path.exists():
        write_jsonl(jsonl_path, [])
        _success(f"Created {jsonl_path}")
    else:
        _warning(f"Already exists: {jsonl_path}")
    _success(f"Initialized playlist folder: {folder}")


@cli.command()
@click.argument("playlist", type=click.Path(exists=True), shell_complete=_complete_dirs)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(),
    help="Output folder (defaults to <playlist>-exported/)",
)
def export(playlist, output):
    """Export MKA tracks back to their original audio format."""
    from yoto_lib.mka import apply_source_patch, extract_audio

    logger.debug("command: export playlist=%s output=%s", playlist, output)
    playlist_path = Path(playlist)
    output_path = Path(output) if output else playlist_path.parent / f"{playlist_path.name}-exported"
    output_path.mkdir(parents=True, exist_ok=True)

    from yoto_cli.progress import _console
    from yoto_cli.progress import success as _success

    mka_files = sorted(playlist_path.glob("*.mka"))
    if not mka_files:
        _console.print("[dim]No .mka files found.[/dim]")
        return

    import shutil
    import tempfile
    from contextlib import nullcontext

    from yoto_cli.progress import make_progress
    from yoto_lib.mka import PATCH_ATTACHMENT_NAME, get_attachment

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
                            # Patch failed -- copy the extraction as fallback
                            shutil.copy2(extracted, final_path)
                            _pcon(f"  {mka.name} -> {final_path.name}")
                else:
                    # No patch -- extract directly to output
                    extracted = extract_audio(mka, output_path)
                    _pcon(f"  {mka.name} -> {extracted.name}")
                if progress and inner_task is not None:
                    progress.update(inner_task, advance=1)
                    progress.remove_task(inner_task)
            except Exception as exc:
                if progress and inner_task is not None:
                    progress.remove_task(inner_task)
                _pcon(f"  [red]x[/red] Error exporting {mka.name}: {exc}")
            if progress and task is not None:
                progress.update(task, advance=1, status=mka.name)

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = [executor.submit(_export_one, mka) for mka in mka_files]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    _pcon(f"  [red]x[/red] Unexpected export error: {exc}")

    _success(f"Exported {len(mka_files)} tracks to {output_path}")


@cli.command(name="list")
def list_cmd():
    """Show all MYO cards on your Yoto account."""
    logger.debug("command: list")
    api = YotoAPI()
    cards = api.get_my_content()

    from rich.table import Table

    from yoto_cli.progress import _console

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

    from yoto_cli.progress import _console
    from yoto_cli.progress import success as _success

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
