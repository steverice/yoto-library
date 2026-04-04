"""Yoto CLI — manage CYO playlists as folders on disk."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import click
from click.shell_completion import CompletionItem
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


def _complete_mka_with_icon(ctx, param, incomplete):
    """Complete .mka files that have a custom icon."""
    return _complete_path(
        incomplete, lambda p: p.suffix.lower() == ".mka" and _has_custom_icon(p)
    )


def _complete_mka_without_icon(ctx, param, incomplete):
    """Complete .mka files that lack a custom icon."""
    return _complete_path(
        incomplete, lambda p: p.suffix.lower() == ".mka" and not _has_custom_icon(p)
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
@click.argument("path", default=".", type=click.Path(exists=True), shell_complete=_complete_weblocs)
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
@click.argument("source", type=click.Path(exists=True), shell_complete=_complete_dirs)
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


def _icon_to_ansi_rows(img: "Image.Image") -> list[str]:
    """Render a 16x16 RGBA image as ANSI rows using half-block characters.

    Each row encodes 2 vertical pixels using ▀ with
    foreground = top pixel, background = bottom pixel.
    """
    img = img.convert("RGBA")
    w, h = img.size
    rows = []
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
        rows.append(row)
    return rows


def _render_icons_side_by_side(
    images: list["Image.Image"],
    labels: list[str],
    footers: list[str] | None = None,
    gap: int = 3,
) -> str:
    """Render multiple icons side-by-side with labels above and optional footers below."""
    import re

    def _pad(text: str, width: int) -> str:
        visible_len = len(re.sub(r"\033\[[^m]*m", "", text))
        return text + " " * max(0, width - visible_len)

    all_rows = [_icon_to_ansi_rows(img) for img in images]
    max_height = max(len(r) for r in all_rows) if all_rows else 0
    icon_width = images[0].size[0] if images else 16
    for rows in all_rows:
        while len(rows) < max_height:
            rows.append(" " * icon_width)

    spacer = " " * gap
    lines = []

    # Label row
    lines.append(spacer.join(_pad(l, icon_width) for l in labels))

    # Image rows
    for y in range(max_height):
        lines.append(spacer.join(_pad(all_rows[i][y], icon_width) for i in range(len(all_rows))))

    # Footer row
    if footers:
        lines.append(spacer.join(_pad(f, icon_width) for f in footers))

    return "\n".join(lines)


@cli.command(name="select-icon")
@click.argument("track", type=click.Path(exists=True), shell_complete=_complete_mka_without_icon)
def select_icon(track):
    """Generate 3 icon options for a track, show best Yoto match, and attach the chosen one."""
    import io
    import tempfile
    from PIL import Image
    from yoto_lib.icons import generate_retrodiffusion_batch, download_icon
    from yoto_lib.icon_catalog import get_catalog
    from yoto_lib.icon_llm import match_icon_llm, compare_icons_llm

    track_path = Path(track)
    title = track_path.stem
    use_tqdm = sys.stderr.isatty()

    # Get best Yoto icon match
    api = YotoAPI()
    catalog = get_catalog(api)

    if use_tqdm:
        from tqdm import tqdm
        pbar = tqdm(total=3, desc=title, bar_format="{desc}: {bar} {n_fmt}/{total_fmt} {postfix}")
        pbar.set_postfix_str("matching Yoto icon")
    else:
        pbar = None

    yoto_media_id, yoto_confidence = match_icon_llm(title, catalog)
    yoto_img: "Image.Image | None" = None
    yoto_title: str | None = None
    yoto_bytes: bytes | None = None

    if yoto_media_id:
        yoto_bytes = download_icon(yoto_media_id)
        if yoto_bytes:
            yoto_img = Image.open(io.BytesIO(yoto_bytes)).convert("RGBA").resize((16, 16), Image.NEAREST)
            for icon in catalog:
                if icon.get("mediaId") == yoto_media_id:
                    yoto_title = icon.get("title", "") or icon.get("name", "")
                    break

    if pbar:
        pbar.update(1)
        pbar.set_postfix_str("generating icons")

    tmpdir = Path(tempfile.mkdtemp(prefix="yoto-icon-"))

    while True:
        batch = generate_retrodiffusion_batch(title, count=3)
        if not batch:
            if pbar:
                pbar.close()
            raise click.ClickException("Icon generation failed")

        if pbar:
            pbar.update(1)
            pbar.set_postfix_str("evaluating icons")

        icons_16: list[Image.Image] = []
        raw_bytes_list: list[bytes] = []
        images_to_show: list[Image.Image] = []
        labels_to_show: list[str] = []
        for i, (raw_bytes, processed_img) in enumerate(batch):
            icons_16.append(processed_img)
            raw_bytes_list.append(raw_bytes)
            images_to_show.append(processed_img)
            labels_to_show.append(f"[{i + 1}] AI")

        if yoto_img is not None:
            images_to_show.append(yoto_img)
            labels_to_show.append(f"[4] \"{yoto_title}\"")
            max_choice = 4
            prompt_text = "Pick an icon (1-4, or 'r' to regenerate)"
        else:
            max_choice = 3
            prompt_text = "Pick an icon (or 'r' to regenerate)"

        # LLM comparison
        winner, scores = compare_icons_llm(
            title, raw_bytes_list,
            yoto_icon=yoto_bytes if yoto_img is not None else None,
        )

        if pbar:
            pbar.update(1)
            pbar.close()
            pbar = None

        # Build score labels
        score_labels = []
        for i in range(len(images_to_show)):
            score = f"{scores[i]:.1f}" if i < len(scores) else "?"
            marker = " *" if (i + 1) == winner else ""
            score_labels.append(f"{'score: ' + score + marker}")

        click.echo(_render_icons_side_by_side(images_to_show, labels_to_show, score_labels))
        click.echo()

        default_choice = str(winner) if 1 <= winner <= max_choice else "1"
        raw = click.prompt(prompt_text, default=default_choice)
        if raw.lower() == "r":
            if use_tqdm:
                from tqdm import tqdm
                pbar = tqdm(total=2, desc=title, bar_format="{desc}: {bar} {n_fmt}/{total_fmt} {postfix}")
                pbar.set_postfix_str("generating icons")
            continue

        try:
            choice = int(raw)
            if not 1 <= choice <= max_choice:
                raise ValueError
        except ValueError:
            click.echo("Invalid choice.")
            continue

        if choice == 4 and yoto_img is not None:
            chosen = yoto_img
        else:
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
@click.argument("tracks", nargs=-1, required=True, type=click.Path(exists=True), shell_complete=_complete_mka_with_icon)
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


# ── completions ──────────────────────────────────────────────────────────────


@cli.command()
@click.argument("shell", required=False, default=None, type=click.Choice(["zsh", "bash", "fish"]))
def completions(shell):
    """Install context-aware shell completions."""
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

    # Check if already installed
    if config.exists() and marker in config.read_text(encoding="utf-8"):
        click.echo(f"Completions already installed in {config}")
        return

    # Append to config
    config.parent.mkdir(parents=True, exist_ok=True)
    with open(config, "a", encoding="utf-8") as f:
        f.write(f"\n{marker}\n{line}\n")

    click.echo(f"Installed completions in {config}")
    click.echo(f"Run this to activate now:  source {config}")


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
