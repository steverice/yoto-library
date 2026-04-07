from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.text import Text

_console = Console(stderr=True)


class CostColumn(ProgressColumn):
    """Show running cost total on the right side of the progress bar."""

    max_refresh = 0.5

    def render(self, task):
        from yoto_lib.costs import get_tracker
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


def spinner_status(msg: str):
    """Context manager: ora-style spinner for single-step waits."""
    return _console.status(msg, spinner="dots")


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


# ── Icon panel rendering ──────────────────────────────────────────────────────


def _icon_to_ansi_rows(img: "object") -> list[str]:
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


def render_icon_panels(
    images: list,
    labels: list[str],
    scores: list[str],
    winner: int,
    selected: int = 0,
) -> Columns:
    """Return a rich Columns of Panel objects, one per icon.

    Args:
        images: PIL Image objects (16×16 RGBA)
        labels: Text labels for each panel title
        scores: Score strings for each panel subtitle (empty string = no score)
        winner: 1-based index of the winning icon (gets ★ marker)
        selected: 0-based index of the currently highlighted icon (gets cyan border)
    """
    panels = []
    for i, (img, label, score) in enumerate(zip(images, labels, scores)):
        ansi_rows = _icon_to_ansi_rows(img)
        body = Text.from_ansi("\n".join(ansi_rows))
        if score:
            body.append(f"\n{score}", style="dim")
        marker = " ★" if (i + 1) == winner else ""
        border = "bold cyan" if i == selected else "dim"
        # Icon is 16 chars wide; panel adds 2 border + 2 padding = 20
        panels.append(Panel(body, title=f"{label}{marker}", border_style=border, width=20))
    return Columns(panels, expand=False, padding=(0, 1))


def _read_key() -> str:
    """Read a single keypress from stdin. Returns 'left', 'right', 'enter', 'r', or the raw char."""
    import sys
    import tty
    import termios
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
    """Interactive icon selector with arrow keys. Returns the user's choice as a string.

    Arrow keys move the highlight, Enter confirms, 'r' regenerates.
    Returns a string: "1"-"N" for a choice, or "r" for regenerate.
    """
    from rich.live import Live
    from rich.text import Text as RichText

    selected = winner - 1  # 0-indexed, start on LLM winner
    hint = RichText("← → to move, Enter to select, r to regenerate", style="dim")

    def _render():
        panels = render_icon_panels(images, labels, scores, winner, selected)
        from rich.console import Group
        return Group(panels, RichText(""), hint)

    import sys
    if not sys.stdin.isatty():
        # Non-interactive: fall back to returning the winner
        _console.print(render_icon_panels(images, labels, scores, winner, selected))
        return str(winner)

    with Live(_render(), console=_console, transient=True) as live:
        while True:
            key = _read_key()
            if key == "left":
                selected = max(0, selected - 1)
                live.update(_render())
            elif key == "right":
                selected = min(max_choice - 1, selected + 1)
                live.update(_render())
            elif key == "enter":
                break
            elif key == "r":
                selected = -1
                break

    # Print final state (non-transient)
    if selected >= 0:
        _console.print(render_icon_panels(images, labels, scores, winner, selected))
        return str(selected + 1)
    return "r"
