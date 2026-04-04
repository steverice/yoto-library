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
from yoto_lib.description import generate_description
from yoto_lib.sync import sync_path
from yoto_lib.pull import pull_playlist
from yoto_lib.playlist import read_jsonl, write_jsonl, scan_audio_files, load_playlist, diff_playlists
from yoto_lib.mka import wrap_in_mka, remove_attachment, set_attachment, read_source_tags, write_tags
from yoto_lib.sources import resolve_weblocs


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
@click.option("--no-trim", is_flag=True, help="Skip silence trimming on YouTube downloads")
def sync(path, dry_run, no_trim):
    """Push local playlist state to Yoto."""
    trim = not no_trim
    if dry_run:
        results = sync_path(Path(path), dry_run=True, trim=trim)
        for result in results:
            icon_msg = f", {result.icons_uploaded} icons" if result.icons_uploaded else ""
            click.echo(f"[Dry run] Would upload {result.tracks_uploaded} tracks{icon_msg}")
            for error in result.errors:
                click.echo(f"Error: {error}", err=True)
        return

    if sys.stderr.isatty():
        from tqdm import tqdm

        # Load playlist to get total track count for the progress bar
        from yoto_lib.playlist import load_playlist as _load
        playlist = _load(Path(path))
        total = len(playlist.track_files)
        # Steps: icons + uploads + cover + save
        pbar = tqdm(total=total * 2 + 2, desc=playlist.title, bar_format="{desc}: {bar} {n_fmt}/{total_fmt}")
        step = [0]

        def log(msg: str):
            pbar.set_postfix_str(msg, refresh=True)
            tqdm.write(msg)
            step[0] += 1
            pbar.n = min(step[0], pbar.total)
            pbar.refresh()

        results = sync_path(Path(path), dry_run=False, trim=trim, log=log)
        pbar.n = pbar.total
        pbar.refresh()
        pbar.close()
    else:
        results = sync_path(Path(path), dry_run=False, trim=trim, log=lambda msg: click.echo(msg))

    for result in results:
        icon_msg = f", {result.icons_uploaded} icons" if result.icons_uploaded else ""
        click.echo(
            f"Done! card {result.card_id}: {result.tracks_uploaded} tracks{icon_msg}"
        )
        for error in result.errors:
            click.echo(f"Error: {error}", err=True)


# ── download ─────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--no-trim", is_flag=True, help="Skip silence trimming on YouTube downloads")
def download(path, no_trim):
    """Download audio from .webloc URLs in a playlist folder."""
    trim = not no_trim
    folder = Path(path)
    created = resolve_weblocs(folder, trim=trim)

    if not created:
        click.echo("No .webloc files resolved.")
        return

    for mka_path in created:
        click.echo(f"  Downloaded: {mka_path.name}")
    click.echo(f"Downloaded {len(created)} tracks.")


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


def _pull_one(folder: Path, card_id: str | None = None, dry_run: bool = False) -> None:
    """Pull a single playlist."""
    def on_track(title: str):
        click.echo(f"  Downloaded: {title}")

    result = pull_playlist(folder, card_id=card_id, dry_run=dry_run, on_track_done=on_track)

    if dry_run:
        click.echo(f"[Dry run] {result.card_id}")
    else:
        icon_msg = f", {result.icons_downloaded} icons" if result.icons_downloaded else ""
        click.echo(
            f"Done! {result.card_id}: {result.tracks_downloaded} tracks{icon_msg}"
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
    api = YotoAPI()
    cards = api.get_my_content()

    if not cards:
        click.echo("No cards found.")
        return

    for card in cards:
        card_id = card.get("cardId", "")
        title = card.get("title", card_id)
        click.echo(f"Pulling {title}...")
        folder = Path(".") / title
        folder.mkdir(exist_ok=True)
        _pull_one(folder, card_id=card_id, dry_run=dry_run)


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
                # Copy metadata from source file to MKA
                source_tags = read_source_tags(audio)
                if source_tags:
                    write_tags(mka_dest, source_tags)
                filenames.append(mka_name)
                click.echo(f"  Wrapped {audio.name} -> {mka_name}")
                if source_path == output_path:
                    audio.unlink()
            except Exception as exc:
                click.echo(f"  Error wrapping {audio.name}: {exc}", err=True)

    write_jsonl(output_path / "playlist.jsonl", filenames)
    click.echo(f"Imported {len(filenames)} tracks into {output_path}")

    # Generate description from track metadata
    playlist = load_playlist(output_path)
    generate_description(playlist, log=lambda msg: click.echo(msg))


# ── select-icon ──────────────────────────────────────────────────────────


def _render_icon_ansi(img: "Image.Image", label: str = "") -> str:
    """Render a 16x16 RGBA image as ANSI art using half-block characters.

    Each character cell encodes 2 vertical pixels using ▀ with
    foreground = top pixel, background = bottom pixel.
    """
    img = img.convert("RGBA")
    w, h = img.size
    lines = []
    if label:
        lines.append(label)
    for y in range(0, h, 2):
        row = ""
        for x in range(w):
            top = img.getpixel((x, y))
            bot = img.getpixel((x, y + 1)) if y + 1 < h else (0, 0, 0, 0)
            if top[3] == 0 and bot[3] == 0:
                row += " "
            elif top[3] == 0:
                row += f"\033[48;2;{bot[0]};{bot[1]};{bot[2]}m\033[38;2;{bot[0]};{bot[1]};{bot[2]}m▄\033[0m"
            elif bot[3] == 0:
                row += f"\033[38;2;{top[0]};{top[1]};{top[2]}m▀\033[0m"
            else:
                row += f"\033[38;2;{top[0]};{top[1]};{top[2]}m\033[48;2;{bot[0]};{bot[1]};{bot[2]}m▀\033[0m"
        lines.append(row)
    return "\n".join(lines)


@cli.command(name="select-icon")
@click.argument("track", type=click.Path(exists=True))
def select_icon(track):
    """Generate 3 icon options for a track and attach the chosen one."""
    import io
    import tempfile
    from PIL import Image
    from yoto_lib.icons import _build_pixelart_prompt, ICON_SIZE, remove_solid_background, nearest_neighbor_upscale
    from yoto_lib.image_providers.retrodiffusion_provider import RetroDiffusionProvider

    track_path = Path(track)
    title = track_path.stem

    click.echo(f"Generating 3 icons for: {title}")

    provider = RetroDiffusionProvider()
    prompt = _build_pixelart_prompt(title)

    tmpdir = Path(tempfile.mkdtemp(prefix="yoto-icon-"))

    while True:
        icons_16: list[Image.Image] = []

        for i in range(3):
            try:
                image_bytes = provider.generate(prompt, ICON_SIZE, ICON_SIZE)
            except Exception as exc:
                raise click.ClickException(f"Icon generation failed: {exc}") from exc

            img = Image.open(io.BytesIO(image_bytes))
            img = remove_solid_background(img)
            icons_16.append(img)

            click.echo(_render_icon_ansi(img, label=f"  [{i + 1}]"))
            click.echo()

        raw = click.prompt("Pick an icon (or 'r' to regenerate)", default="1")
        if raw.lower() == "r":
            click.echo("Regenerating...")
            continue

        try:
            choice = int(raw)
            if not 1 <= choice <= 3:
                raise ValueError
        except ValueError:
            click.echo("Invalid choice.")
            continue

        chosen = icons_16[choice - 1]
        break

    buf = io.BytesIO()
    chosen.save(buf, format="PNG")
    icon_bytes = buf.getvalue()

    icon_tmp = tmpdir / "chosen_icon.png"
    icon_tmp.write_bytes(icon_bytes)
    set_attachment(track_path, icon_tmp, name="icon", mime_type="image/png")

    from yoto_lib.icons import set_macos_file_icon
    set_macos_file_icon(track_path, chosen)
    click.echo(f"Attached icon to {track_path.name}")

    icon_tmp.unlink(missing_ok=True)
    tmpdir.rmdir()


# ── reset-icon ───────────────────────────────────────────────────────────


@cli.command(name="reset-icon")
@click.argument("tracks", nargs=-1, required=True, type=click.Path(exists=True))
def reset_icon(tracks):
    """Remove the icon from one or more MKA tracks so sync regenerates them."""
    from yoto_lib.icons import clear_macos_file_icon

    for track in tracks:
        path = Path(track)
        try:
            remove_attachment(path, "icon")
            clear_macos_file_icon(path)
            click.echo(f"  Cleared icon: {path.name}")
        except Exception as exc:
            click.echo(f"  Error ({path.name}): {exc}", err=True)


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
