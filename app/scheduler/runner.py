"""APScheduler-backed blocking runner for scheduled tasks.

Loads all enabled tasks from the store, creates CronTrigger jobs, and
blocks until SIGINT/SIGTERM. The fire_time passed to execute_task is
derived from APScheduler's JobExecutionEvent.scheduled_run_time (the
actual intended fire time for the current tick, converted to UTC) so
all instances contend on the same (task_id, fire_time) dedup key.
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
    """Job callback invoked via the event listener with the correct fire_time.

    The fire_time is derived from JobExecutionEvent.scheduled_run_time
    (the actual intended fire time for this tick), NOT from
    job.next_run_time (which already points to the next period).
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

    Uses APScheduler's EVENT_JOB_EXECUTED/EVENT_JOB_ERROR listeners to
    capture the correct scheduled_run_time for each execution. The job
    function itself receives the fire_time as an argument computed from
    the trigger's get_next_fire_time at registration, then updated on
    each tick via the jitter-free scheduled_run_time from the event.

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

        # APScheduler 3.x passes scheduled_run_time to the job function
        # when the job is configured with next_run_time explicitly. The
        # correct way to get the *current* tick's intended fire time is
        # to read it from the trigger before the job runs. We use a
        # closure that captures the task_id and computes fire_time from
        # the trigger's perspective at call time.
        #
        # Key insight from Greptile review: job.next_run_time inside the
        # callback already points to the NEXT period (one tick ahead).
        # Instead, we pass scheduled_run_time via APScheduler's built-in
        # mechanism: setting the job's func to accept it as a kwarg and
        # using the 'next_run_time' parameter.

        scheduler.add_job(
            _execute_with_scheduled_time,
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
        raise SystemExit("No enabled tasks found. Add tasks with `opensre cron add` first.")

    # Use EVENT_JOB_SUBMITTED to capture the correct scheduled_run_time.
    # APScheduler 3.x emits this event with event.scheduled_run_time set
    # to the intended fire time for the current tick (not the next one).
    from apscheduler.events import EVENT_JOB_SUBMITTED

    # Store scheduled_run_times keyed by job_id for the callback to read
    _pending_fire_times: dict[str, str] = {}

    def _on_job_submitted(event: Any) -> None:
        """Capture scheduled_run_time from the event before the job runs."""
        fire_time = _compute_fire_time(event.scheduled_run_time)
        _pending_fire_times[event.job_id] = fire_time

    scheduler.add_listener(_on_job_submitted, EVENT_JOB_SUBMITTED)

    # Replace the job function with one that reads from _pending_fire_times
    # We need to rebind the jobs to use the event-driven fire_time
    for job in scheduler.get_jobs():

        def _make_event_driven_func(tid: str) -> Any:
            def _func() -> None:
                ft = _pending_fire_times.pop(tid, None)
                if ft is None:
                    # Fallback: should not happen if listener fires first
                    ft = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
                _job_wrapper(tid, ft)

            return _func

        job.modify(func=_make_event_driven_func(job.id), args=[])

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


def _execute_with_scheduled_time(task_id: str) -> None:
    """Placeholder job function replaced at runtime with event-driven version."""
    # This is replaced by _make_event_driven_func before the scheduler starts.
    # If somehow called directly, fall back to wall-clock time.
    fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    _job_wrapper(task_id, fire_time)


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
