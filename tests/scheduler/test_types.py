"""Tests for scheduler type models."""

from __future__ import annotations

from app.scheduler.types import ScheduledTask, TaskKind, TaskRun, TaskStatus


class TestScheduledTask:
    def test_defaults(self) -> None:
        task = ScheduledTask(kind=TaskKind.DAILY_SUMMARY, cron="0 9 * * 1-5", provider="telegram")
        assert len(task.id) == 12
        assert task.timezone == "UTC"
        assert task.enabled is True
        assert task.window_hours == 24
        assert task.last_run is None

    def test_roundtrip(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            timezone="Europe/London",
            provider="slack",
            chat_id="C01234",
            window_hours=168,
        )
        data = task.model_dump(mode="json")
        restored = ScheduledTask.model_validate(data)
        assert restored.kind == TaskKind.WEEKLY_AUDIT
        assert restored.cron == "0 8 * * 1"
        assert restored.timezone == "Europe/London"


class TestTaskRun:
    def test_defaults(self) -> None:
        run = TaskRun(task_id="abc123")
        assert run.status == TaskStatus.PENDING
        assert run.error == ""
        assert run.posted_message_id == ""

    def test_failed_run(self) -> None:
        run = TaskRun(task_id="x", status=TaskStatus.FAILED, error="timeout")
        assert run.status == TaskStatus.FAILED
        assert run.error == "timeout"
