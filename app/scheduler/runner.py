"""APScheduler-backed blocking runner for scheduled tasks.

Loads all enabled tasks from the store, creates CronTrigger jobs, and
blocks until SIGINT/SIGTERM. The fire_time passed to execute_task is
derived from the trigger's computed next_fire_time (converted to UTC)
so all instances contend on the same (task_id, fire_time) dedup key.
"""

from __future__ import annotations

import logging
import signal
import threading
from datetime import UTC, datetime
from typing import Any

from app.scheduler.executor import execute_task
from app.scheduler.store import get_task, list_tasks, update_task
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


def _job_wrapper(task_id: str, fire_time: str) -> None:
    """Job callback invoked by APScheduler.

    Receives fire_time as a pre-computed UTC string passed via add_job(kwargs=...).
    APScheduler 3.x does NOT auto-inject scheduled_run_time into callbacks,
    so we compute the stable dedup key in a listener and pass it explicitly.
    """
    task = get_task(task_id)
    if task is None:
        logger.warning("Task %s not found in store, skipping", task_id)
        return
    if not task.enabled:
        logger.info("Task %s is disabled, skipping", task_id)
        return

    result = execute_task(task, fire_time)

    # Update last_run in the store on success
    if result:
        task.last_run = datetime.now(UTC).isoformat()
        update_task(task)


def _compute_fire_time(scheduled_run_time: Any) -> str:
    """Compute a stable, UTC-normalized fire_time string from APScheduler's run time.

    Always converts to UTC so DST transitions don't produce ambiguous keys.
    """
    if scheduled_run_time is not None:
        # Convert to UTC for unambiguous dedup key
        utc_time: datetime = scheduled_run_time.astimezone(UTC)
        return utc_time.strftime("%Y-%m-%dT%H:%MZ")
    # Fallback: should not happen in normal APScheduler flow
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")


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

        # APScheduler 3.x does NOT auto-inject scheduled_run_time into job
        # callbacks. We compute fire_time inside the job function by reading
        # the job's next_run_time from the scheduler at execution time.

        def _make_job_func(tid: str) -> Any:
            """Create a job function that computes fire_time from the job's next_run_time."""

            def _func() -> None:
                # Get the job to access its next_run_time for stable dedup
                job = scheduler.get_job(tid)
                if job and job.next_run_time:
                    ft = _compute_fire_time(job.next_run_time)
                else:
                    ft = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
                _job_wrapper(tid, ft)

            return _func

        scheduler.add_job(
            _make_job_func(task.id),
            trigger=trigger,
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
        raise SystemExit("No enabled tasks found. Add tasks with `opensre cron add` first.")

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

    Uses the current time with seconds precision as fire_time so it does
    not conflict with scheduled runs (which use minute precision).
    """
    task = get_task(task_id)
    if task is None:
        return False

    fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return execute_task(task, fire_time)


__all__ = ["run_task_now", "start_scheduler"]
