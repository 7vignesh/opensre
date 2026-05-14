"""Tests for scheduler task execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.scheduler import claim_store as claim_module
from app.scheduler import store as store_module
from app.scheduler.claim_store import get_runs
from app.scheduler.executor import execute_task
from app.scheduler.store import add_task, get_task
from app.scheduler.types import ScheduledTask, TaskKind, TaskStatus


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "SCHEDULER_STORE_PATH", tmp_path / "scheduler.json")
    monkeypatch.setattr(claim_module, "SCHEDULER_DB_PATH", tmp_path / "scheduler.db")


class TestExecuteTask:
    def test_task_not_found(self) -> None:
        run = execute_task("nonexistent")
        assert run.status == TaskStatus.FAILED
        assert "not found" in run.error.lower()

    def test_telegram_missing_credentials(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider="telegram",
            chat_id="-100123",
            params={},
        )
        add_task(task)
        run = execute_task(task.id)
        assert run.status == TaskStatus.FAILED
        assert "bot_token" in run.error.lower()

    def test_successful_telegram_delivery(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider="telegram",
            chat_id="-100123",
            params={"bot_token": "123:ABC"},
        )
        add_task(task)

        with patch("app.utils.telegram_delivery.post_telegram_message") as mock_post:
            mock_post.return_value = (True, "", "msg_42")
            run = execute_task(task.id)

        assert run.status == TaskStatus.SUCCESS
        assert run.posted_message_id == "msg_42"
        updated = get_task(task.id)
        assert updated is not None
        assert updated.last_run is not None

    def test_failed_delivery_records_error(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            provider="telegram",
            chat_id="-100123",
            params={"bot_token": "123:ABC"},
        )
        add_task(task)

        with patch("app.utils.telegram_delivery.post_telegram_message") as mock_post:
            mock_post.return_value = (False, "rate limited", "")
            run = execute_task(task.id)

        assert run.status == TaskStatus.FAILED
        assert "rate limited" in run.error

    def test_unsupported_provider(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider="whatsapp",
            chat_id="123",
        )
        add_task(task)
        run = execute_task(task.id)
        assert run.status == TaskStatus.FAILED
        assert "unsupported" in run.error.lower()

    def test_run_persisted_in_sqlite(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider="telegram",
            chat_id="-100123",
            params={"bot_token": "123:ABC"},
        )
        add_task(task)

        with patch("app.utils.telegram_delivery.post_telegram_message") as mock_post:
            mock_post.return_value = (True, "", "msg_99")
            execute_task(task.id)

        runs = get_runs(task.id)
        assert len(runs) == 1
        assert runs[0].status == TaskStatus.SUCCESS
        assert runs[0].posted_message_id == "msg_99"
