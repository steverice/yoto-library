from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

_console = Console(stderr=True)


def make_progress() -> Progress:
    """Return a configured rich Progress instance (renders on stderr)."""
    return Progress(
        SpinnerColumn("dots"),
        TextColumn("[bold]{task.description}"),
        BarColumn(complete_style="bold cyan", finished_style="bold green", pulse_style="cyan"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("[dim]{task.fields[status]}"),
        console=_console,
        transient=False,
    )


def spinner_status(msg: str):
    """Context manager: ora-style spinner for single-step waits."""
    return _console.status(msg, spinner="dots")
