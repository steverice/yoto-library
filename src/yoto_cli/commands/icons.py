"""select-icon and reset-icon commands."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import click

from yoto_cli.main import _complete_mka_with_icon, _complete_mka_without_icon, _print_cost_summary, cli
from yoto_lib.billing.costs import reset_tracker
from yoto_lib.mka import remove_attachment
from yoto_lib.yoto.api import YotoAPI

logger = logging.getLogger(__name__)


@cli.command(name="select-icon")
@click.argument(
    "tracks", nargs=-1, required=True, type=click.Path(exists=True), shell_complete=_complete_mka_without_icon
)
def select_icon(tracks: tuple[str, ...]) -> None:
    """Generate 3 icon options per track, show best Yoto match, and attach the chosen one."""
    logger.debug("command: select-icon tracks=%s", tracks)
    from rich.rule import Rule

    from yoto_cli.iterm_colors import ensure_srgb, restore_colors, show_hint_if_needed
    from yoto_cli.progress import _console, interactive_icon_select, make_progress
    from yoto_cli.progress import success as _success
    from yoto_cli.progress import warning as _warning
    from yoto_lib.icons.select import IconSelectionRound, select_icons_for_tracks

    reset_tracker()

    api = YotoAPI()
    track_paths = [Path(t) for t in tracks]

    # -- Mutable state shared between callbacks --
    progress_ctx = [None]  # [Progress context manager]
    progress_ref = [None]  # [Progress instance]
    task_ref = [None]  # [main task id]
    inner_tasks: dict[str, int] = {}  # key -> rich task id
    icon_tasks: dict[int, int] = {}  # icon index -> rich task id
    iterm_originals_ref = [None]
    iterm_hint_needed = [False]

    def _open_progress(title: str) -> None:
        ctx = make_progress()
        progress_ref[0] = ctx.__enter__(None, None, None) if False else ctx.__enter__()
        progress_ctx[0] = ctx
        task_ref[0] = progress_ref[0].add_task(title, total=6, status="matching Yoto icon")

    def _close_progress() -> None:
        if progress_ctx[0] is not None:
            progress_ctx[0].__exit__(None, None, None)
            progress_ctx[0] = None
            progress_ref[0] = None
            task_ref[0] = None
            inner_tasks.clear()
            icon_tasks.clear()

    def _on_track_start(idx: int, total: int, track_path: Path) -> None:
        if total > 1:
            _console.print(Rule(title=f"{track_path.name} ({idx + 1}/{total})"))
        _open_progress(track_path.stem)

    def _on_step(status: str) -> None:
        p = progress_ref[0]
        t = task_ref[0]
        if p is not None and t is not None:
            p.update(t, advance=1, status=status)

    def _on_inner(label: str | None, key: str) -> None:
        p = progress_ref[0]
        if p is None:
            return
        if label is None:
            tid = inner_tasks.pop(key, None)
            if tid is not None:
                p.remove_task(tid)
        else:
            inner_tasks[key] = p.add_task(label, total=None, status="")

    def _on_generation_progress(done_n: int) -> None:
        p = progress_ref[0]
        t = task_ref[0]
        if p is not None and t is not None:
            if done_n < 3:
                p.update(t, advance=1, status=f"generating icon {done_n + 1}/3")
            else:
                p.update(t, advance=1, status="evaluating icons")

    def _on_icon_gen_start(i: int, desc: str) -> None:
        p = progress_ref[0]
        if p is not None:
            icon_tasks[i] = p.add_task(f"Icon {i + 1}: {desc}", total=None, status="")

    def _on_icon_gen_done(i: int) -> None:
        p = progress_ref[0]
        if p is not None and i in icon_tasks:
            p.remove_task(icon_tasks.pop(i))

    def _on_scores_missing() -> None:
        _warning("Icon evaluation timed out, scores unavailable")

    def _on_round_ready() -> None:
        _close_progress()
        iterm_originals_ref[0] = ensure_srgb()
        iterm_hint_needed[0] = not iterm_originals_ref[0]

    def _on_round_cleanup() -> None:
        if iterm_originals_ref[0]:
            restore_colors(iterm_originals_ref[0])
        elif iterm_hint_needed[0]:
            show_hint_if_needed()

    def _choose_icon(round_result: IconSelectionRound) -> str:
        candidates = round_result.candidates
        images = [c.image for c in candidates]
        labels = [f"[{j + 1}] {c.label}" for j, c in enumerate(candidates)]
        score_labels = []
        for j, c in enumerate(candidates):
            if c.is_existing:
                score_labels.append("")
            else:
                score = f"{c.score:.1f}" if c.score is not None else "?"
                marker = " *" if (j + 1) == round_result.winner else ""
                score_labels.append(f"score: {score}{marker}")

        return interactive_icon_select(
            images,
            labels,
            score_labels,
            round_result.winner,
            len(candidates),
        )

    def _on_applied(track_path: Path) -> None:
        _success(f"Attached icon to {track_path.name}")

    def _on_skipped(track_path: Path) -> None:
        _close_progress()
        _console.print(f"[dim]Keeping current icon for {track_path.name}[/dim]")

    def _on_error(msg: str) -> None:
        p = progress_ref[0]
        if p is not None:
            p.console.print(f"[red]x[/red] {msg}")
        else:
            _console.print(f"[red]x[/red] {msg}")

    select_icons_for_tracks(
        track_paths,
        api,
        on_step=_on_step,
        on_inner=_on_inner,
        on_generation_progress=_on_generation_progress,
        on_icon_gen_start=_on_icon_gen_start,
        on_icon_gen_done=_on_icon_gen_done,
        on_warn=_warning,
        on_error=_on_error,
        choose_icon=_choose_icon,
        on_track_start=_on_track_start,
        on_round_ready=_on_round_ready,
        on_round_cleanup=_on_round_cleanup,
        on_scores_missing=_on_scores_missing,
        on_applied=_on_applied,
        on_skipped=_on_skipped,
    )

    _print_cost_summary()


@cli.command(name="reset-icon")
@click.argument("tracks", nargs=-1, required=True, type=click.Path(exists=True), shell_complete=_complete_mka_with_icon)
def reset_icon(tracks: tuple[str, ...]) -> None:
    """Remove the icon from one or more MKA tracks so sync regenerates them."""
    logger.debug("command: reset-icon tracks=%s", tracks)
    from yoto_cli.progress import error as _error
    from yoto_cli.progress import success as _success
    from yoto_lib.icons import clear_macos_file_icon

    for track in tracks:
        path = Path(track)
        try:
            remove_attachment(path, "icon")
            clear_macos_file_icon(path)
            _success(f"Cleared icon: {path.name}")
        except (subprocess.CalledProcessError, OSError) as exc:
            _error(f"Error ({path.name}): {exc}")
