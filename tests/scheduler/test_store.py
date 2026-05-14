"""Tests for scheduler store persistence (task definitions)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.scheduler import claim_store as claim_module
from app.scheduler import store as store_module
from app.scheduler.store import add_task, get_task, list_tasks, remove_task, update_task
from app.scheduler.types import ScheduledTask, TaskKind


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "SCHEDULER_STORE_PATH", tmp_path / "scheduler.json")
    monkeypatch.setattr(claim_module, "SCHEDULER_DB_PATH", tmp_path / "scheduler.db")


class TestTaskCRUD:
    def test_add_and_list(self) -> None:
        task = ScheduledTask(kind=TaskKind.DAILY_SUMMARY, cron="0 9 * * *", provider="telegram")
        add_task(task)
        tasks = list_tasks()
        assert len(tasks) == 1
        assert tasks[0].id == task.id

    def test_get_task(self) -> None:
        task = ScheduledTask(kind=TaskKind.WEEKLY_AUDIT, cron="0 8 * * 1", provider="slack")
        add_task(task)
        found = get_task(task.id)
        assert found is not None
        assert found.kind == TaskKind.WEEKLY_AUDIT

    def test_get_task_not_found(self) -> None:
        assert get_task("nonexistent") is None

    def test_remove_task(self) -> None:
        task = ScheduledTask(kind=TaskKind.DAILY_SUMMARY, cron="0 9 * * *", provider="discord")
        add_task(task)
        assert remove_task(task.id) is True
        assert list_tasks() == []

    def test_remove_nonexistent(self) -> None:
        assert remove_task("ghost") is False

    def test_update_task(self) -> None:
        task = ScheduledTask(kind=TaskKind.DAILY_SUMMARY, cron="0 9 * * *", provider="telegram")
        add_task(task)
        task.enabled = False
        task.last_run = "2026-01-01T00:00:00+00:00"
        update_task(task)
        reloaded = get_task(task.id)
        assert reloaded is not None
        assert reloaded.enabled is False
        assert reloaded.last_run == "2026-01-01T00:00:00+00:00"

    def test_multiple_tasks(self) -> None:
        for i in range(3):
            add_task(
                ScheduledTask(kind=TaskKind.DAILY_SUMMARY, cron=f"{i} 9 * * *", provider="telegram")
            )
        assert len(list_tasks()) == 3
