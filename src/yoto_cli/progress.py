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
        return Text(f"${total:.2f}", style="dim cyan")


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
