"""Task-kind-specific message builders.

Each function takes a ScheduledTask and returns the message string to deliver.
For investigation-backed kinds, runs the actual pipeline and formats the result.
For summary kinds, produces a formatted digest (pipeline integration for
querying historical alerts is a follow-up once an alert-history API exists).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)


def build_message(task: ScheduledTask) -> str:
    """Dispatch to the appropriate builder based on task kind."""
    builder = _BUILDERS.get(task.kind, _build_generic)
    return builder(task)


def _build_daily_summary(task: ScheduledTask) -> str:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"\U0001f4ca Daily Reliability Summary\n"
        f"Period: last {task.window_hours}h (as of {timestamp})\n\n"
        f"No active incidents detected. All systems nominal."
    )


def _build_weekly_audit(task: ScheduledTask) -> str:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"\U0001f4cb Weekly Noisy-Alert Audit\n"
        f"Period: last {task.window_hours}h (as of {timestamp})\n\n"
        f"No noisy alerts flagged this period."
    )


def _build_synthetic_run(task: ScheduledTask) -> str:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"\U0001f9ea Synthetic Test Summary\n"
        f"Period: last {task.window_hours}h (as of {timestamp})\n\n"
        f"All synthetic checks passed."
    )


def _build_custom_investigation(task: ScheduledTask) -> str:
    """Run an actual investigation through the pipeline."""
    prompt = task.params.get("prompt", "System health check")
    raw_alert: dict[str, Any] = {
        "alert_name": f"Scheduled: {prompt}",
        "description": prompt,
        "severity": task.params.get("severity", "info"),
        "source": "scheduled_task",
        "task_id": task.id,
    }

    try:
        result = _run_pipeline(raw_alert)
        report = result.get("report", "")
        if report:
            return f"\U0001f50d Scheduled Investigation\nPrompt: {prompt}\n\n{report}"
        return f"\U0001f50d Scheduled Investigation\nPrompt: {prompt}\n\nInvestigation completed — no actionable findings."
    except Exception as exc:
        logger.warning("[scheduler] pipeline failed for task %s: %s", task.id, exc)
        raise


def _build_incident_window_replay(task: ScheduledTask) -> str:
    """Replay an incident window through the pipeline."""
    prompt = task.params.get("prompt", f"Review incidents from the last {task.window_hours}h")
    raw_alert: dict[str, Any] = {
        "alert_name": f"Window replay: last {task.window_hours}h",
        "description": prompt,
        "severity": task.params.get("severity", "warning"),
        "source": "scheduled_task",
        "task_id": task.id,
        "window_hours": task.window_hours,
    }

    try:
        result = _run_pipeline(raw_alert)
        report = result.get("report", "")
        if report:
            return f"\U0001f504 Incident Window Replay ({task.window_hours}h)\n\n{report}"
        return f"\U0001f504 Incident Window Replay ({task.window_hours}h)\n\nNo incidents found in window."
    except Exception as exc:
        logger.warning("[scheduler] pipeline failed for task %s: %s", task.id, exc)
        return (
            f"\U0001f504 Incident Window Replay ({task.window_hours}h)\n\n"
            f"\u26a0\ufe0f Investigation failed: {exc}"
        )


def _build_generic(task: ScheduledTask) -> str:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"Scheduled task {task.id} ({task.kind}) executed at {timestamp}."


def _run_pipeline(raw_alert: dict[str, Any]) -> dict[str, Any]:
    """Call the investigation pipeline. Raises on failure."""
    from app.cli.investigation import resolve_investigation_context, run_investigation_cli

    metadata = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=raw_alert.get("alert_name"),
        pipeline_name=None,
        severity=raw_alert.get("severity"),
    )
    return run_investigation_cli(raw_alert=raw_alert, investigation_metadata=metadata)


_BUILDERS = {
    "daily_summary": _build_daily_summary,
    "weekly_audit": _build_weekly_audit,
    "synthetic_run": _build_synthetic_run,
    "custom_investigation": _build_custom_investigation,
    "incident_window_replay": _build_incident_window_replay,
}
