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
from rich.style import Style
from rich.table import Table
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
                result.append("▄", style=Style(
                    color=f"rgb({bot[0]},{bot[1]},{bot[2]})",
                    bgcolor=f"rgb({bot[0]},{bot[1]},{bot[2]})",
                ))
            elif bot[3] == 0:
                result.append("▀", style=Style(
                    color=f"rgb({top[0]},{top[1]},{top[2]})",
                ))
            else:
                result.append("▀", style=Style(
                    color=f"rgb({top[0]},{top[1]},{top[2]})",
                    bgcolor=f"rgb({bot[0]},{bot[1]},{bot[2]})",
                ))
    return result


def render_icon_panels(
    images: list,
    labels: list[str],
    scores: list[str],
    winner: int,
) -> Table:
    """Return a rich Table displaying icons side by side with labels and scores.

    Args:
        images: PIL Image objects (16×16 RGBA)
        labels: Text labels for each column header
        scores: Score strings for each icon (empty string = no score)
        winner: 1-based index of the winning icon (gets ★ marker + cyan)
    """
    from rich import box

    table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2), expand=False, show_edge=False)

    for i, label in enumerate(labels):
        is_winner = (i + 1) == winner
        marker = " ★" if is_winner else ""
        table.add_column(
            f"{label}{marker}",
            no_wrap=True,
            header_style="bold cyan" if is_winner else "",
        )

    cells = []
    for i, (img, score) in enumerate(zip(images, scores)):
        body = _icon_to_rich_text(img)
        if score:
            is_winner = (i + 1) == winner
            body.append(f"\n{score}", style="bold cyan" if is_winner else "dim")
        cells.append(body)

    table.add_row(*cells)
    return table
