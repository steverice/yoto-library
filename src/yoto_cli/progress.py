from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TextColumn,
    TimeElapsedColumn,
)
from rich.style import Style
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from PIL import Image

_console = Console(stderr=True)


class CostColumn(ProgressColumn):
    """Show running cost total on the right side of the progress bar."""

    max_refresh = 0.5

    def render(self, task: Task) -> Text:
        from yoto_lib.billing.costs import get_tracker

        total = get_tracker().total
        if total == 0:
            return Text("")
        return Text(f"${total:.3f}", style="dim cyan")


def make_progress() -> Progress:
    """Return a configured rich Progress instance (renders on stderr)."""
    return Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold]{task.description}"),
        BarColumn(complete_style="bold cyan", finished_style="bold green", pulse_style="cyan"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("[dim]{task.fields[status]}"),
        CostColumn(),
        console=_console,
        transient=False,
    )


# ── Message helpers ───────────────────────────────────────────────────────────


def success(msg: str) -> None:
    """Print a green ✓ success message to stderr."""
    _console.print(f"[green]✓ {msg}[/green]")


def error(msg: str) -> None:
    """Print a red ✗ error message to stderr."""
    _console.print(f"[red]✗ {msg}[/red]")


def warning(msg: str) -> None:
    """Print a yellow ⚠ warning message to stderr."""
    _console.print(f"[yellow]⚠ {msg}[/yellow]")


# ── Icon rendering ───────────────────────────────────────────────────────────


def _icon_to_rich_text(img: Image.Image) -> Text:
    """Render a 16x16 RGBA image as a rich Text using half-block characters."""
    img = img.convert("RGBA")
    w, h = img.size
    result = Text(overflow="fold", no_wrap=True)
    for y in range(0, h, 2):
        if y > 0:
            result.append("\n")
        for x in range(w):
            top: tuple[int, int, int, int] = img.getpixel((x, y))  # ty: ignore[invalid-assignment]
            bot: tuple[int, int, int, int] = img.getpixel((x, y + 1)) if y + 1 < h else (0, 0, 0, 0)  # ty: ignore[invalid-assignment]
            if top[3] == 0 and bot[3] == 0:
                result.append(" ")
            elif top[3] == 0:
                result.append(
                    "▄",
                    style=Style(
                        color=f"rgb({bot[0]},{bot[1]},{bot[2]})",
                        bgcolor=f"rgb({bot[0]},{bot[1]},{bot[2]})",
                    ),
                )
            elif bot[3] == 0:
                result.append(
                    "▀",
                    style=Style(
                        color=f"rgb({top[0]},{top[1]},{top[2]})",
                    ),
                )
            elif top[:3] == bot[:3]:
                # Same color — use full block to avoid fg/bg seam entirely
                result.append(
                    "█",
                    style=Style(
                        color=f"rgb({top[0]},{top[1]},{top[2]})",
                    ),
                )
            else:
                result.append(
                    "▀",
                    style=Style(
                        color=f"rgb({top[0]},{top[1]},{top[2]})",
                        bgcolor=f"rgb({bot[0]},{bot[1]},{bot[2]})",
                    ),
                )
    return result


def render_icon_panels(
    images: list,
    labels: list[str],
    scores: list[str],
    winner: int,
    selected: int = -1,
) -> Table:
    """Return a rich Table displaying icons side by side with labels and scores.

    Args:
        images: PIL Image objects (16x16 RGBA)
        labels: Text labels for each column header
        scores: Score strings for each icon (empty string = no score)
        winner: 1-based index of the winning icon (gets ★ marker)
        selected: 0-based index of the currently selected icon (gets cyan highlight).
                  -1 means use winner as the highlighted one.
    """
    from rich import box

    if selected < 0:
        selected = winner - 1

    table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2), expand=False, show_edge=False)

    for i, label in enumerate(labels):
        marker = " ★" if winner > 0 and (i + 1) == winner else ""
        is_sel = i == selected
        table.add_column(
            f"{label}{marker}",
            width=24,
            justify="center",
            header_style="bold cyan" if is_sel else "",
        )

    cells = []
    for i, (img, score) in enumerate(zip(images, scores, strict=False)):
        body = _icon_to_rich_text(img)
        if score:
            is_sel = i == selected
            body.append(f"\n{score}", style="bold cyan" if is_sel else "dim")
        cells.append(body)

    table.add_row(*cells)
    return table


def _read_key() -> str:
    """Read a single keypress from stdin. Returns 'left', 'right', 'enter', 'r', or the raw char."""
    import sys
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            ch3 = sys.stdin.read(1)
            if ch2 == "[":
                if ch3 == "D":
                    return "left"
                if ch3 == "C":
                    return "right"
            return "escape"
        if ch in ("\r", "\n"):
            return "enter"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _try_read_key(timeout: float = 0.1) -> str | None:
    """Non-blocking keypress read in raw mode. Returns None if no input arrives within timeout."""
    import select
    import sys
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        if not select.select([sys.stdin], [], [], timeout)[0]:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            ch3 = sys.stdin.read(1)
            if ch2 == "[":
                if ch3 == "D":
                    return "left"
                if ch3 == "C":
                    return "right"
            return "escape"
        if ch in ("\r", "\n"):
            return "enter"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def interactive_icon_select(
    images: list,
    labels: list[str],
    scores: list[str],
    winner: int,
    max_choice: int,
    scores_future: Any | None = None,
) -> str:
    """Interactive icon selector with arrow keys.

    Prints icons via _console.print() (static, no Live). On arrow key,
    erases output with ANSI cursor-up + clear-to-end, then reprints.

    If scores_future is provided, polls it every 0.1 s. When it resolves,
    updates score labels and winner; if the user has not pressed any arrow
    key yet, auto-jumps the cursor to the winning icon.

    Returns "1"-"N" for a choice, or "r" for regenerate.
    """
    import sys

    # scores is a mutable list so we can update it in-place when eval arrives
    cur_scores = list(scores)
    cur_winner = winner
    selected = 0
    user_has_moved = False
    scores_applied = False

    hint = "[dim]← → to move, Enter to select, r to regenerate[/dim]"

    def _print_and_count() -> int:
        with _console.capture() as cap:
            _console.print(render_icon_panels(images, labels, cur_scores, cur_winner, selected))
            _console.print(hint)
        rendered = cap.get()
        _console.file.write(rendered)
        _console.file.flush()
        return rendered.count("\n")

    if not sys.stdin.isatty():
        # Non-interactive: block on eval result before displaying
        if scores_future is not None:
            _apply_scores(cur_scores, cur_winner, scores_future, max_choice)
        _console.print(render_icon_panels(images, labels, cur_scores, cur_winner, 0))
        return str(cur_winner) if cur_winner > 0 else "1"

    line_count = _print_and_count()

    while True:
        # Poll for a keypress with a short timeout so we can check the future
        key = _try_read_key(0.1)

        if key is None:
            # No keypress — check whether eval has finished
            if not scores_applied and scores_future is not None and scores_future.done():
                new_winner, new_scores = scores_future.result()
                scores_applied = True
                if new_scores:
                    _fill_scores(cur_scores, new_scores, new_winner, max_choice)
                    cur_winner = new_winner
                    if not user_has_moved and new_winner > 0:
                        selected = new_winner - 1
                else:
                    # Eval timed out — replace placeholders with "?"
                    for i in range(len(cur_scores)):
                        if cur_scores[i] == "scoring\u2026":
                            cur_scores[i] = "?"
                _console.file.write(f"\033[{line_count}A\033[J")
                _console.file.flush()
                line_count = _print_and_count()
            continue

        if key == "enter":
            _console.file.write(f"\033[{line_count}A\033[J")
            _console.file.flush()
            _console.print(render_icon_panels(images, labels, cur_scores, cur_winner, selected))
            return str(selected + 1)
        if key == "r":
            _console.file.write(f"\033[{line_count}A\033[J")
            _console.file.flush()
            return "r"
        if key == "left":
            user_has_moved = True
            selected = max(0, selected - 1)
        elif key == "right":
            user_has_moved = True
            selected = min(max_choice - 1, selected + 1)
        else:
            continue

        _console.file.write(f"\033[{line_count}A\033[J")
        _console.file.flush()
        line_count = _print_and_count()


def _fill_scores(
    cur_scores: list[str],
    new_scores: list[float],
    winner: int,
    max_choice: int,
) -> None:
    """Update cur_scores in-place with formatted score strings."""
    for i in range(min(len(cur_scores), len(new_scores))):
        marker = " *" if (i + 1) == winner else ""
        cur_scores[i] = f"score: {new_scores[i]:.1f}{marker}"


def _apply_scores(
    cur_scores: list[str],
    cur_winner: int,
    scores_future: Any,
    max_choice: int,
) -> int:
    """Block on scores_future and update cur_scores. Returns the winner index."""
    new_winner, new_scores = scores_future.result()
    if new_scores:
        _fill_scores(cur_scores, new_scores, new_winner, max_choice)
        return new_winner
    for i in range(len(cur_scores)):
        if cur_scores[i] == "scoring\u2026":
            cur_scores[i] = "?"
    return cur_winner
