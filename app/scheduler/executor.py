"""Task execution: runs a scheduled task and delivers the result."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from app.scheduler.claim_store import save_run, try_claim
from app.scheduler.store import get_task, update_task
from app.scheduler.tasks import build_message
from app.scheduler.types import ScheduledTask, TaskRun, TaskStatus
from app.utils.truncation import truncate

logger = logging.getLogger(__name__)

_TELEGRAM_LIMIT = 4096
_DISCORD_LIMIT = 2000


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
            task_id=task_id, status=TaskStatus.PENDING, error="claimed by another instance"
        )

    _emit_analytics("started", task)
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

    task.last_run = run.started_at
    update_task(task)

    duration_ms = int((time.monotonic() - start) * 1000)
    if run.status == TaskStatus.SUCCESS:
        _emit_analytics("completed", task, duration_ms=duration_ms)
    else:
        _emit_analytics("failed", task, duration_ms=duration_ms, error=run.error)

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


def _resolve_credentials(task: ScheduledTask) -> dict[str, str]:
    """Resolve provider credentials from task params or integration store."""
    # First check task.params for explicit credentials
    if task.params.get("bot_token") or task.params.get("access_token"):
        return dict(task.params)

    # Fall back to the integration store
    from app.integrations.store import get_integration

    record = get_integration(task.provider)
    if record:
        return record.get("credentials", {})
    return {}


def _deliver(task: ScheduledTask, message: str) -> tuple[bool, str, str]:
    """Deliver a message to the configured provider.

    Resolves credentials from task.params first, then falls back to the
    integration store. Applies platform-specific message length limits.

    Returns (success, error, message_id).
    """
    provider = task.provider.lower()
    creds = _resolve_credentials(task)

    if provider == "telegram":
        from app.utils.telegram_delivery import post_telegram_message

        bot_token = creds.get("bot_token", "")
        chat_id = task.chat_id or creds.get("default_chat_id", "")
        if not bot_token or not chat_id:
            return False, "Missing bot_token or chat_id for Telegram", ""
        truncated = truncate(message, _TELEGRAM_LIMIT, suffix="\u2026")
        return post_telegram_message(chat_id, truncated, bot_token)

    if provider == "slack":
        from app.utils.delivery_transport import post_json
        from app.utils.slack_delivery import _slack_bearer_headers

        token = creds.get("access_token", "") or creds.get("bot_token", "")
        channel = task.chat_id
        if not token or not channel:
            return False, "Missing access_token or channel for Slack", ""
        # Post a top-level message (no thread_ts) via chat.postMessage
        payload = {"channel": channel, "text": message}
        response = post_json(
            url="https://slack.com/api/chat.postMessage",
            payload=payload,
            headers=_slack_bearer_headers(token),
        )
        if not response.ok:
            return False, response.error or f"HTTP {response.status_code}", ""
        if response.data.get("ok") is not True:
            return False, str(response.data.get("error", "unknown")), ""
        msg_ts = str(response.data.get("ts", ""))
        return True, "", msg_ts

    if provider == "discord":
        from app.utils.discord_delivery import post_discord_message

        bot_token = creds.get("bot_token", "")
        channel_id = task.chat_id or creds.get("default_channel_id", "")
        if not bot_token or not channel_id:
            return False, "Missing bot_token or channel_id for Discord", ""
        truncated = truncate(message, _DISCORD_LIMIT, suffix="\u2026")
        return post_discord_message(channel_id, embeds=[], bot_token=bot_token, content=truncated)

    return False, f"Unsupported provider: {provider}", ""


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def _emit_analytics(
    phase: str,
    task: ScheduledTask,
    *,
    duration_ms: int = 0,
    error: str = "",
) -> None:
    """Emit a scheduled-task analytics event. Failures are silently logged."""
    try:
        from app.analytics.events import Event
        from app.analytics.provider import get_analytics

        event_map = {
            "started": Event.SCHEDULED_TASK_STARTED,
            "completed": Event.SCHEDULED_TASK_COMPLETED,
            "failed": Event.SCHEDULED_TASK_FAILED,
        }
        event = event_map.get(phase)
        if event is None:
            return
        props: dict[str, object] = {
            "task_id": task.id,
            "task_kind": task.kind,
            "provider": task.provider,
        }
        if duration_ms:
            props["duration_ms"] = duration_ms
        if error:
            props["error"] = error[:200]
        get_analytics().capture(event, props)
    except Exception:
        # Analytics must never break task execution
        logger.debug("[scheduler] analytics emit failed for %s", phase, exc_info=True)
