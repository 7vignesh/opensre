"""APScheduler-backed blocking runner for scheduled tasks.

Loads all enabled tasks from the store, creates CronTrigger jobs, and
blocks until SIGINT/SIGTERM. The fire_time passed to execute_task is
derived from the scheduler's intended run time (not wall-clock) so all
instances contend on the same (task_id, fire_time) dedup key.
"""

from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime
from typing import Any

from app.scheduler.executor import execute_task
from app.scheduler.store import get_task, list_tasks
from app.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)


def _make_trigger(task: ScheduledTask) -> Any:
    """Build an APScheduler CronTrigger from a task's cron expression and timezone.

    Raises ValueError if the cron expression or timezone is invalid.
    """
    from apscheduler.triggers.cron import CronTrigger

    parts = task.cron.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {task.cron!r}")

    try:
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone=task.timezone,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"Invalid cron/timezone for task {task.id}: {exc}") from exc
    return trigger


def _job_wrapper(task_id: str, scheduled_run_time: datetime | None = None) -> None:
    """Job callback invoked by APScheduler.

    Derives fire_time from the scheduler's intended run time (passed by
    APScheduler as the job's scheduled_run_time) so all instances racing
    for the same tick contend on the same dedup key.
    """
    # APScheduler 3.x passes scheduled_run_time via the job execution context.
    # We receive it as a keyword arg from the scheduler.
    if scheduled_run_time is not None:
        fire_time = scheduled_run_time.strftime("%Y-%m-%dT%H:%M")
    else:
        # Fallback: should not happen in normal APScheduler flow
        from datetime import UTC

        fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")

    task = get_task(task_id)
    if task is None:
        logger.warning("Task %s not found in store, skipping", task_id)
        return
    if not task.enabled:
        logger.info("Task %s is disabled, skipping", task_id)
        return

    execute_task(task, fire_time)


def start_scheduler() -> None:
    """Load all enabled tasks and start the blocking scheduler.

    Blocks until SIGINT or SIGTERM. Invalid tasks (bad cron, bad timezone)
    are logged and skipped rather than crashing the entire daemon.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    tasks = list_tasks()
    enabled_count = 0

    for task in tasks:
        if not task.enabled:
            continue
        try:
            trigger = _make_trigger(task)
        except ValueError as exc:
            logger.error("Skipping task %s: %s", task.id, exc)
            continue

        # APScheduler 3.x: use next_run_time from trigger to pass scheduled_run_time
        scheduler.add_job(
            _job_wrapper,
            trigger=trigger,
            args=[task.id],
            id=task.id,
            name=f"{task.kind.value}:{task.id}",
            replace_existing=True,
            misfire_grace_time=60,
        )
        enabled_count += 1
        logger.info(
            "Registered task %s (%s) with cron=%s tz=%s",
            task.id,
            task.kind,
            task.cron,
            task.timezone,
        )

    if enabled_count == 0:
        logger.warning("No enabled tasks found. Scheduler has nothing to run.")
        return

    # Install shutdown handlers
    stop_event = threading.Event()

    def _shutdown_handler(_signum: int, _frame: Any) -> None:
        stop_event.set()
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown_handler)
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        signal.signal(sigterm, _shutdown_handler)

    logger.info("Scheduler started with %d task(s). Waiting for triggers...", enabled_count)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


def run_task_now(task_id: str) -> bool:
    """Execute a task immediately (ad-hoc one-shot for debugging).

    Uses the current time as fire_time so it does not conflict with
    scheduled runs.
    """
    from datetime import UTC

    task = get_task(task_id)
    if task is None:
        return False

    fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return execute_task(task, fire_time)


__all__ = ["run_task_now", "start_scheduler"]
