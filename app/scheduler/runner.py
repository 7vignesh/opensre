"""APScheduler-backed cron runner for scheduled tasks."""

from __future__ import annotations

import logging
import signal
import sys
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

from app.scheduler.executor import execute_task
from app.scheduler.store import list_tasks, update_task
from app.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)


def _make_trigger(task: ScheduledTask) -> CronTrigger:
    """Parse a 5-field cron expression into an APScheduler CronTrigger."""
    parts = task.cron.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {task.cron!r}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=task.timezone,
    )


def _job_wrapper(task_id: str) -> None:
    """Wrapper called by APScheduler for each tick.

    The fire_time is truncated to the minute so all instances processing
    the same cron tick converge on the same claim key. APScheduler fires
    jobs within seconds of the scheduled time, so minute-level granularity
    is sufficient for dedup.
    """
    fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")
    execute_task(task_id, fire_time=fire_time)


def start_scheduler() -> None:
    """Load all enabled tasks and block until SIGINT/SIGTERM.

    Each task is registered as an APScheduler job with its cron trigger.
    The scheduler uses an in-memory jobstore (task definitions live in our
    JSON store; APScheduler only handles timing). Execution dedup is
    handled by the SQLite claim store.
    """
    tasks = [t for t in list_tasks() if t.enabled]
    if not tasks:
        logger.info("[scheduler] no enabled tasks — nothing to schedule")
        return

    scheduler = BlockingScheduler()

    for task in tasks:
        try:
            trigger = _make_trigger(task)
        except ValueError as exc:
            logger.warning("[scheduler] skipping task %s: %s", task.id, exc)
            continue

        scheduler.add_job(
            _job_wrapper,
            trigger=trigger,
            args=[task.id],
            id=task.id,
            name=f"{task.kind}:{task.provider}",
            replace_existing=True,
        )

        next_fire = trigger.get_next_fire_time(None, datetime.now(UTC))
        if next_fire:
            task.next_run = next_fire.isoformat()
            update_task(task)

        logger.info(
            "[scheduler] registered task=%s kind=%s cron=%r tz=%s next=%s",
            task.id,
            task.kind,
            task.cron,
            task.timezone,
            task.next_run,
        )

    def _shutdown(signum: int, _frame: object) -> None:
        logger.info("[scheduler] received signal %d, shutting down", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _shutdown)

    logger.info("[scheduler] starting with %d task(s)", len(tasks))
    scheduler.start()
