"""Scheduled task and run types."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from app.strict_config import StrictConfigModel


class TaskKind(StrEnum):
    """Supported scheduled task kinds."""

    DAILY_SUMMARY = "daily_summary"
    WEEKLY_AUDIT = "weekly_audit"
    INCIDENT_WINDOW_REPLAY = "incident_window_replay"
    SYNTHETIC_RUN = "synthetic_run"
    CUSTOM_INVESTIGATION = "custom_investigation"


class TaskStatus(StrEnum):
    """Execution status of a task run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ScheduledTask(StrictConfigModel):
    """A recurring task definition persisted in the scheduler store."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: TaskKind
    cron: str  # Standard 5-field cron expression
    timezone: str = "UTC"
    provider: str  # telegram, slack, discord
    chat_id: str = ""
    window_hours: int = 24
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_run: str | None = None
    next_run: str | None = None


class TaskRun(StrictConfigModel):
    """Record of a single task execution."""

    task_id: str
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    posted_message_id: str = ""
    error: str = ""
