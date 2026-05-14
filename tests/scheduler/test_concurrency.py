"""Integration test: concurrent execution claims."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.scheduler import claim_store as claim_module
from app.scheduler import store as store_module
from app.scheduler.claim_store import try_claim
from app.scheduler.executor import execute_task
from app.scheduler.store import add_task
from app.scheduler.types import ScheduledTask, TaskKind, TaskStatus


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "SCHEDULER_STORE_PATH", tmp_path / "scheduler.json")
    monkeypatch.setattr(claim_module, "SCHEDULER_DB_PATH", tmp_path / "scheduler.db")


class TestClaimDedup:
    def test_single_claim_succeeds(self) -> None:
        assert try_claim("task_1", "2026-01-01T09:00") is True

    def test_duplicate_claim_fails(self) -> None:
        assert try_claim("task_2", "2026-01-01T09:00") is True
        assert try_claim("task_2", "2026-01-01T09:00") is False

    def test_different_fire_times_both_succeed(self) -> None:
        assert try_claim("task_3", "2026-01-01T09:00") is True
        assert try_claim("task_3", "2026-01-01T10:00") is True

    def test_concurrent_claims_only_one_wins(self) -> None:
        """Two sequential claims for the same slot — second one fails.

        In production, concurrent instances are separate processes. SQLite's
        UNIQUE constraint guarantees only one INSERT succeeds regardless of
        timing. We test the constraint directly rather than racing threads
        (which share the same connection pool on Windows).
        """
        assert try_claim("race_task", "2026-01-01T09:00") is True
        # Simulate a second instance trying the same slot
        assert try_claim("race_task", "2026-01-01T09:00") is False


class TestExecuteWithClaim:
    def test_second_execution_skipped(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider="telegram",
            chat_id="-100",
            params={"bot_token": "123:ABC"},
        )
        add_task(task)

        with patch("app.utils.telegram_delivery.post_telegram_message") as mock_post:
            mock_post.return_value = (True, "", "msg_1")
            run1 = execute_task(task.id, fire_time="2026-01-01T09:00")
            run2 = execute_task(task.id, fire_time="2026-01-01T09:00")

        assert run1.status == TaskStatus.SUCCESS
        # Second run is skipped (claimed by another)
        assert "claimed" in run2.error
        # Only one delivery call
        assert mock_post.call_count == 1

    def test_no_fire_time_skips_claim(self) -> None:
        """When fire_time is None (ad-hoc run), no claim check happens."""
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider="telegram",
            chat_id="-100",
            params={"bot_token": "123:ABC"},
        )
        add_task(task)

        with patch("app.utils.telegram_delivery.post_telegram_message") as mock_post:
            mock_post.return_value = (True, "", "msg_1")
            run1 = execute_task(task.id)
            run2 = execute_task(task.id)

        # Both succeed since no claim dedup
        assert run1.status == TaskStatus.SUCCESS
        assert run2.status == TaskStatus.SUCCESS
        assert mock_post.call_count == 2
