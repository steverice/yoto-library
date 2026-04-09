from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.style import Style
from rich.table import Table
from rich.text import Text

_console = Console(stderr=True)


class CostColumn(ProgressColumn):
    """Show running cost total on the right side of the progress bar."""

    max_refresh = 0.5

    def render(self, task):
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


def _icon_to_rich_text(img: "object") -> Text:
    """Render a 16x16 RGBA image as a rich Text using half-block characters."""
    img = img.convert("RGBA")
    w, h = img.size
    result = Text(overflow="fold", no_wrap=True)
    for y in range(0, h, 2):
        if y > 0:
            result.append("\n")
        for x in range(w):
            top = img.getpixel((x, y))
            bot = img.getpixel((x, y + 1)) if y + 1 < h else (0, 0, 0, 0)
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
        images: PIL Image objects (16×16 RGBA)
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
        marker = " ★" if (i + 1) == winner else ""
        is_sel = i == selected
        table.add_column(
            f"{label}{marker}",
            width=24,
            justify="center",
            header_style="bold cyan" if is_sel else "",
        )

    cells = []
    for i, (img, score) in enumerate(zip(images, scores)):
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
        tty.setraw(fd)
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
) -> str:
    """Interactive icon selector with arrow keys.

    Prints icons via _console.print() (static, no Live). On arrow key,
    erases output with ANSI cursor-up + clear-to-end, then reprints.
    Returns "1"-"N" for a choice, or "r" for regenerate.
    """
    import sys

    selected = winner - 1
    hint = "[dim]← → to move, Enter to select, r to regenerate[/dim]"

    def _print_and_count() -> int:
        """Print the icon display and hint, return number of lines printed."""
        with _console.capture() as cap:
            _console.print(render_icon_panels(images, labels, scores, winner, selected))
            _console.print(hint)
        rendered = cap.get()
        _console.file.write(rendered)
        _console.file.flush()
        return rendered.count("\n")

    if not sys.stdin.isatty():
        _console.print(render_icon_panels(images, labels, scores, winner, selected))
        return str(winner)

    line_count = _print_and_count()

    while True:
        key = _read_key()
        if key == "enter":
            # Erase and reprint final state (without hint)
            _console.file.write(f"\033[{line_count}A\033[J")
            _console.file.flush()
            _console.print(render_icon_panels(images, labels, scores, winner, selected))
            return str(selected + 1)
        elif key == "r":
            # Erase display
            _console.file.write(f"\033[{line_count}A\033[J")
            _console.file.flush()
            return "r"
        elif key == "left":
            selected = max(0, selected - 1)
        elif key == "right":
            selected = min(max_choice - 1, selected + 1)
        else:
            continue

        # Erase and reprint with new selection
        _console.file.write(f"\033[{line_count}A\033[J")
        _console.file.flush()
        line_count = _print_and_count()
