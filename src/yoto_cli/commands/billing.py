"""providers command — status, balances, and cost tracking."""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import click

from yoto_lib.billing import (
    fetch_balances, fetch_subscription_usage, read_totals, reset_totals,
    PROVIDER_GROUPS, DASHBOARD_URLS,
)

from yoto_cli.main import cli

logger = logging.getLogger(__name__)

def _check_all_status() -> dict[str, tuple[bool | None, str | None]]:
    """Check status of all providers. Returns {name: (healthy, status_page_host)}."""
    from yoto_lib.providers import get_active_providers

    results: dict[str, tuple[bool | None, str | None]] = {}

    for cls in get_active_providers():
        name = cls.display_name

        status = cls.check_status()
        if status is None:
            results[name] = (None, None)
        else:
            host = status.url
            if not host and hasattr(cls, "status_page_url"):
                host = urlparse(cls.status_page_url).hostname
            results[name] = (status.healthy, host)

    return results


@cli.command("providers")
@click.option(
    "--reset",
    "reset_group",
    is_flag=False,
    flag_value="__all__",
    default=None,
    help="Reset lifetime cost data. Optionally specify a provider group.",
)
def providers(reset_group):
    """Show provider status, balances, and lifetime costs."""
    logger.debug("command: providers reset=%s", reset_group)

    # Handle --reset
    if reset_group is not None:
        if reset_group == "__all__":
            if not click.confirm("This will reset all lifetime cost tracking. Continue?"):
                return
            reset_totals()
            click.echo("Reset all lifetime billing data.")
        else:
            if reset_group not in PROVIDER_GROUPS:
                valid = ", ".join(sorted(PROVIDER_GROUPS))
                raise click.UsageError(f"Unknown provider group '{reset_group}'. Valid: {valid}")
            reset_totals(reset_group)
            click.echo(f"Reset lifetime billing data for {reset_group}.")
        return

    # Fetch live data in parallel
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as pool:
        status_future = pool.submit(_check_all_status)
        balance_future = pool.submit(fetch_balances)
        usage_future = None
        from yoto_lib.providers.claude_provider import ClaudeProvider
        if ClaudeProvider().is_subscription:
            usage_future = pool.submit(fetch_subscription_usage)

    statuses = status_future.result()
    balances = balance_future.result()
    subscription_usage = usage_future.result() if usage_future else None

    # Section 1: Status
    _print_status(statuses)
    click.echo()

    # Section 2: Balances
    _print_balances(balances)
    click.echo()

    # Section 3: Subscription
    if subscription_usage:
        _print_subscription_usage(subscription_usage)
        click.echo()

    # Section 4: Lifetime spend
    _print_lifetime_spend()


def _print_status(statuses: dict[str, tuple[bool | None, str | None]]) -> None:
    """Print the Status section."""
    from rich.table import Table
    from yoto_cli.progress import _console

    table = Table(title="Status", title_style="bold", title_justify="left", show_header=False, box=None, padding=(0, 1))
    table.add_column(style="cyan", min_width=26)
    table.add_column()
    table.add_column(style="dim")

    for name, (healthy, host) in statuses.items():
        if healthy is None:
            table.add_row(name, "[dim]--[/dim]", "")
        elif healthy:
            table.add_row(name, "[green]ok[/green]", host or "")
        else:
            table.add_row(name, "[red]degraded[/red]", host or "")

    _console.print(table)


def _print_balances(balances: dict) -> None:
    """Print the Balances section."""
    from rich.table import Table
    from yoto_cli.progress import _console

    from yoto_lib.providers import get_active_providers
    active_providers = [cls.display_name for cls in get_active_providers()]

    if not active_providers:
        return

    table = Table(title="Balances", title_style="bold", title_justify="left", show_header=False, box=None, padding=(0, 1))
    table.add_column(style="cyan", min_width=26)
    table.add_column()

    for name in active_providers:
        if name in balances:
            info = balances[name]
            if "balance" in info:
                table.add_row(name, f"${info['balance']:.2f} remaining")
            elif "error" in info:
                table.add_row(name, f"[yellow]error: {info['error']}[/yellow]")
        elif name in DASHBOARD_URLS:
            table.add_row(name, f"[dim]check dashboard \u2192 {DASHBOARD_URLS[name]}[/dim]")

    _console.print(table)


def _print_subscription_usage(usage: dict) -> None:
    """Print the Subscription section."""
    from datetime import datetime, timezone
    from rich.table import Table
    from yoto_cli.progress import _console

    table = Table(title="Subscription", title_style="bold", title_justify="left", show_header=False, box=None, padding=(0, 1))
    table.add_column(style="cyan", min_width=26)
    table.add_column(justify="right")
    table.add_column(style="dim")

    for key, label in [("session", "Claude (session)"), ("weekly", "Claude (weekly)"), ("weekly_sonnet", "Claude (weekly Sonnet)")]:
        if key not in usage:
            continue
        info = usage[key]
        pct = info["utilization"]
        resets_at = info.get("resets_at", "")

        # Format reset time as relative
        reset_str = ""
        if resets_at:
            try:
                reset_dt = datetime.fromisoformat(resets_at)
                now = datetime.now(timezone.utc)
                delta = reset_dt - now
                if delta.total_seconds() > 0:
                    hours = int(delta.total_seconds() // 3600)
                    if hours >= 24:
                        reset_str = f"resets {reset_dt.strftime('%b %d')}"
                    else:
                        reset_str = f"resets in {hours}h"
            except ValueError:
                pass

        table.add_row(label, f"{pct:.0f}% used", reset_str)

    _console.print(table)


def _print_lifetime_spend() -> None:
    """Print the Lifetime spend section."""
    from rich.table import Table
    from yoto_cli.progress import _console
    from yoto_lib.billing.costs import COSTS
    from yoto_lib.providers.claude_provider import ClaudeProvider
    claude_is_sub = ClaudeProvider().is_subscription

    totals = read_totals()
    if not totals:
        _console.print("[bold]Lifetime spend[/bold]")
        _console.print("  No data yet. Run some commands first.")
        return

    table = Table(title="Lifetime spend", title_style="bold", title_justify="left", show_header=False, box=None, padding=(0, 1))
    table.add_column(style="cyan", min_width=26)
    table.add_column(justify="right")
    table.add_column(style="dim")

    billable_total = 0.0
    for key in COSTS:
        if key not in totals:
            continue
        entry = totals[key]
        label = COSTS[key]["label"]
        calls = entry["calls"]

        if key.startswith("claude_") and claude_is_sub:
            table.add_row(label, "\u2014", f"{calls} calls, subscription")
        else:
            cost = entry["cost"]
            billable_total += cost
            table.add_row(label, f"${cost:.2f}", f"{calls} calls")

    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]${billable_total:.2f}[/bold]", "")

    _console.print(table)
