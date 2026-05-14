"""Task execution: runs a scheduled task and delivers the result."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from app.scheduler.claim_store import save_run, try_claim
from app.scheduler.store import get_task, update_task
from app.scheduler.tasks import build_message
from app.scheduler.types import ScheduledTask, TaskRun, TaskStatus

logger = logging.getLogger(__name__)


def execute_task(task_id: str, *, fire_time: str | None = None) -> TaskRun:
    """Execute a scheduled task by ID.

    If fire_time is provided, attempts to claim the execution slot first.
    If another instance already claimed it, returns a skipped run.
    """
    task = get_task(task_id)
    if task is None:
        run = TaskRun(task_id=task_id, status=TaskStatus.FAILED, error="Task not found")
        save_run(run)
        return run

    # Claim-based dedup: skip if another instance already claimed this tick
    if fire_time and not try_claim(task_id, fire_time):
        logger.debug("[scheduler] task %s already claimed for %s, skipping", task_id, fire_time)
        return TaskRun(
            task_id=task_id, status=TaskStatus.SUCCESS, error="claimed by another instance"
        )

    _emit_started(task)
    run = TaskRun(task_id=task_id, status=TaskStatus.RUNNING)
    start = time.monotonic()

    try:
        message = build_message(task)
        posted, error, message_id = _deliver(task, message)
        if posted:
            run.status = TaskStatus.SUCCESS
            run.posted_message_id = message_id
        else:
            run.status = TaskStatus.FAILED
            run.error = error
    except Exception as exc:
        logger.exception("[scheduler] task %s failed", task_id)
        run.status = TaskStatus.FAILED
        run.error = str(exc)

    run.finished_at = datetime.now(UTC).isoformat()
    save_run(run)

    # Update last_run on the task definition
    task.last_run = run.started_at
    update_task(task)

    duration_ms = int((time.monotonic() - start) * 1000)
    if run.status == TaskStatus.SUCCESS:
        _emit_completed(task, duration_ms)
    else:
        _emit_failed(task, duration_ms, run.error)

    logger.info(
        "[scheduler] task=%s kind=%s provider=%s status=%s duration=%dms",
        task_id,
        task.kind,
        task.provider,
        run.status,
        duration_ms,
    )
    return run


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _deliver(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver a message to the configured provider.

    Returns (success, error, message_id).
    """
    provider = task.provider.lower()

    if provider == "telegram":
        from app.utils.telegram_delivery import post_telegram_message

        bot_token = task.params.get("bot_token", "")
        if not bot_token or not task.chat_id:
            return False, "Missing bot_token or chat_id for Telegram", ""
        return post_telegram_message(task.chat_id, message, bot_token)

    if provider == "slack":
        from app.utils.slack_delivery import send_slack_report

        token = task.params.get("access_token", "")
        if not token or not task.chat_id:
            return False, "Missing access_token or channel for Slack", ""
        ok, err = send_slack_report(message, channel=task.chat_id, access_token=token)
        return ok, err, ""

    if provider == "discord":
        from app.utils.discord_delivery import post_discord_message

        bot_token = task.params.get("bot_token", "")
        if not bot_token or not task.chat_id:
            return False, "Missing bot_token or channel_id for Discord", ""
        return post_discord_message(task.chat_id, embeds=[], bot_token=bot_token, content=message)

    return False, f"Unsupported provider: {provider}", ""


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def _emit_started(task: ScheduledTask) -> None:
    try:
        from app.analytics.events import Event
        from app.analytics.provider import capture

        capture(
            Event.SCHEDULED_TASK_STARTED,
            {"task_id": task.id, "task_kind": task.kind, "provider": task.provider},
        )
    except Exception:
        pass


def _emit_completed(task: ScheduledTask, duration_ms: int) -> None:
    try:
        from app.analytics.events import Event
        from app.analytics.provider import capture

        capture(
            Event.SCHEDULED_TASK_COMPLETED,
            {
                "task_id": task.id,
                "task_kind": task.kind,
                "provider": task.provider,
                "duration_ms": duration_ms,
            },
        )
    except Exception:
        pass


def _emit_failed(task: ScheduledTask, duration_ms: int, error: str) -> None:
    try:
        from app.analytics.events import Event
        from app.analytics.provider import capture

        capture(
            Event.SCHEDULED_TASK_FAILED,
            {
                "task_id": task.id,
                "task_kind": task.kind,
                "provider": task.provider,
                "duration_ms": duration_ms,
                "error": error[:200],
            },
        )
    except Exception:
        pass
