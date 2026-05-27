"""Render sweep results as Rich tables (and plain rows for testing)."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from yoto_lib.tools.trim_tune.scorer import ConfigScore, rank_survivors

_console = Console()


def survivors_vs_tolerance(configs: list[ConfigScore], tolerances: list[float]) -> list[tuple[float, int]]:
    return [(t, sum(1 for c in configs if c.survives(t))) for t in tolerances]


def print_sweep(configs: list[ConfigScore], tolerances: list[float], top_n: int, label_noise: float) -> None:
    summary = Table(title="Survivors vs tolerance")
    summary.add_column("tolerance (s)")
    summary.add_column("survivors")
    for tol, n in survivors_vs_tolerance(configs, tolerances):
        flag = "  (below label noise)" if tol < label_noise else ""
        summary.add_row(f"{tol:.2f}{flag}", str(n))
    _console.print(summary)
    _console.print(f"[dim]measured label-noise floor: {label_noise:.3f}s[/dim]")

    primary = tolerances[len(tolerances) // 2]
    survivors = rank_survivors(configs, primary)
    if not survivors:
        _console.print(
            f"[red]Zero survivors at tolerance {primary:.2f}s.[/red] "
            "Run leave-one-out before concluding silence cannot win."
        )
        for c in sorted(configs, key=lambda c: c.worst_clip.clip)[:top_n]:
            wc = c.worst_clip
            _console.print(f"  killed by [yellow]{wc.filename}[/yellow] (clip {wc.clip:.2f}s): {c.params}")
        return
    table = Table(title=f"Top {top_n} survivors @ tolerance {primary:.2f}s")
    table.add_column("worst leftover (s)")
    table.add_column("total leftover (s)")
    table.add_column("params")
    for c in survivors[:top_n]:
        table.add_row(f"{c.worst_leftover:.2f}", f"{c.total_leftover:.2f}", str(c.params))
    _console.print(table)
