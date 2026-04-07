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
    render_fn: "Callable" = None,
) -> str:
    """Interactive icon selector with arrow keys. Returns the user's choice as a string.

    Arrow keys move the highlight, Enter confirms, 'r' regenerates.
    render_fn(images, labels, scores, selected) -> str returns the ANSI display string.
    Returns a string: "1"-"N" for a choice, or "r" for regenerate.
    """
    from rich.live import Live
    from rich.text import Text as RichText

    selected = winner - 1  # 0-indexed, start on LLM winner

    def _render():
        ansi = render_fn(images, labels, scores, selected=selected)
        return RichText.from_ansi(ansi + "\n← → to move, Enter to select, r to regenerate")

    import sys
    if not sys.stdin.isatty():
        _console.print(RichText.from_ansi(render_fn(images, labels, scores, selected=selected)))
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
        _console.print(RichText.from_ansi(render_fn(images, labels, scores, selected=selected)))
        return str(selected + 1)
    return "r"
