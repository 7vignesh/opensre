"""``opensre cron`` command group: manage scheduled deliveries."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from app.scheduler.claim_store import get_runs
from app.scheduler.executor import execute_task
from app.scheduler.store import add_task, get_task, list_tasks, remove_task
from app.scheduler.types import ScheduledTask, TaskKind

_console = Console(highlight=False)

_KIND_CHOICES = [k.value for k in TaskKind]
_PROVIDER_CHOICES = ["telegram", "slack", "discord"]


@click.group("cron")
def cron() -> None:
    """Manage scheduled recurring deliveries."""


@cron.command("list")
def list_command() -> None:
    """List all scheduled tasks."""
    tasks = list_tasks()
    if not tasks:
        _console.print("[dim]No scheduled tasks. Use `opensre cron add` to create one.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Kind")
    table.add_column("Cron")
    table.add_column("TZ")
    table.add_column("Provider")
    table.add_column("Enabled")
    table.add_column("Last Run")

    for task in tasks:
        table.add_row(
            task.id,
            task.kind,
            task.cron,
            task.timezone,
            task.provider,
            "✓" if task.enabled else "✗",
            task.last_run or "—",
        )
    _console.print(table)


@cron.command("add")
@click.option("--kind", "-k", type=click.Choice(_KIND_CHOICES), required=True)
@click.option(
    "--cron", "-c", "cron_expr", required=True, help='5-field cron expression (e.g. "0 9 * * 1-5")'
)
@click.option("--tz", "timezone", default="UTC", help="IANA timezone (default: UTC)")
@click.option("--provider", "-p", type=click.Choice(_PROVIDER_CHOICES), required=True)
@click.option("--chat-id", required=True, help="Target chat/channel ID")
@click.option("--window", "window_hours", type=int, default=24, help="Lookback window in hours")
@click.option(
    "--token",
    "token",
    default=None,
    help="Provider bot/access token (reads from integration store if omitted)",
)
def add_command(
    kind: str,
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    window_hours: int,
    token: str | None,
) -> None:
    """Add a new scheduled task.

    Provider credentials are resolved from the integration store by default.
    Pass --token explicitly to override.
    """
    from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

    # Validate cron expression by constructing the trigger
    parts = cron_expr.split()
    if len(parts) != 5:
        _console.print("[red]Error: cron expression must have exactly 5 fields.[/red]")
        raise SystemExit(1)
    try:
        minute, hour, day, month, day_of_week = parts
        CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=timezone,
        )
    except (ValueError, TypeError) as exc:
        _console.print(f"[red]Error: invalid cron expression — {exc}[/red]")
        raise SystemExit(1) from None

    # Build params with credentials
    params: dict[str, str] = {}
    if token:
        # Store under the key the executor expects
        key = "access_token" if provider == "slack" else "bot_token"
        params[key] = token

    task = ScheduledTask(
        kind=TaskKind(kind),
        cron=cron_expr,
        timezone=timezone,
        provider=provider,
        chat_id=chat_id,
        window_hours=window_hours,
        params=params,
    )
    add_task(task)
    creds_source = "explicit --token" if token else "integration store (auto-resolved at runtime)"
    _console.print(f"[green]Task {task.id} created.[/green]")
    _console.print(f"  Kind: {task.kind}  Cron: {task.cron}  TZ: {task.timezone}")
    _console.print(f"  Provider: {task.provider}  Chat: {task.chat_id}")
    _console.print(f"  Credentials: {creds_source}")


@cron.command("remove")
@click.argument("task_id")
def remove_command(task_id: str) -> None:
    """Remove a scheduled task by ID."""
    if remove_task(task_id):
        _console.print(f"[green]Task {task_id} removed.[/green]")
    else:
        _console.print(f"[yellow]Task {task_id} not found.[/yellow]")


@cron.command("run")
@click.argument("task_id")
def run_command(task_id: str) -> None:
    """Execute a task immediately (one-shot, for debugging)."""
    task = get_task(task_id)
    if task is None:
        _console.print(f"[red]Task {task_id} not found.[/red]")
        raise SystemExit(1)

    _console.print(f"[dim]Running task {task_id} ({task.kind})...[/dim]")
    result = execute_task(task_id)
    if result.status == "success":
        _console.print(f"[green]Done. Message ID: {result.posted_message_id or 'N/A'}[/green]")
    else:
        _console.print(f"[red]Failed: {result.error}[/red]")


@cron.command("logs")
@click.argument("task_id")
@click.option("--limit", "-n", type=int, default=10, help="Number of runs to show")
def logs_command(task_id: str, limit: int) -> None:
    """Show recent execution history for a task."""
    task = get_task(task_id)
    if task is None:
        _console.print(f"[yellow]Task {task_id} not found.[/yellow]")
        return

    runs = get_runs(task_id, limit=limit)
    if not runs:
        _console.print(f"[dim]No runs recorded for task {task_id}.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Started")
    table.add_column("Status")
    table.add_column("Message ID")
    table.add_column("Error")

    for run in runs:
        status_style = "green" if run.status == "success" else "red"
        table.add_row(
            run.started_at,
            f"[{status_style}]{run.status}[/{status_style}]",
            run.posted_message_id or "—",
            run.error[:60] if run.error else "—",
        )
    _console.print(table)


@cron.command("start")
def start_command() -> None:
    """Start the scheduler daemon (blocks until interrupted)."""
    from app.scheduler.runner import start_scheduler

    _console.print("[dim]Starting scheduler...[/dim]")
    start_scheduler()
