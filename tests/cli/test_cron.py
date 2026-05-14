"""Tests for the `opensre cron` CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from app.cli.commands.cron import cron
from app.scheduler import claim_store as claim_module
from app.scheduler import store as store_module


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "SCHEDULER_STORE_PATH", tmp_path / "scheduler.json")
    monkeypatch.setattr(claim_module, "SCHEDULER_DB_PATH", tmp_path / "scheduler.db")


class TestCronList:
    def test_empty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cron, ["list"])
        assert result.exit_code == 0
        assert "No scheduled tasks" in result.output

    def test_with_tasks(self) -> None:
        runner = CliRunner()
        runner.invoke(
            cron,
            [
                "add",
                "--kind",
                "daily_summary",
                "--cron",
                "0 9 * * *",
                "--provider",
                "telegram",
                "--chat-id",
                "-100",
            ],
        )
        result = runner.invoke(cron, ["list"])
        assert result.exit_code == 0
        assert "daily_summ" in result.output
        assert "telegram" in result.output


class TestCronAdd:
    def test_add_task(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cron,
            [
                "add",
                "--kind",
                "daily_summary",
                "--cron",
                "0 9 * * 1-5",
                "--provider",
                "slack",
                "--chat-id",
                "C01234",
                "--window",
                "48",
            ],
        )
        assert result.exit_code == 0
        assert "created" in result.output.lower()

    def test_invalid_cron(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cron,
            [
                "add",
                "--kind",
                "daily_summary",
                "--cron",
                "invalid",
                "--provider",
                "telegram",
                "--chat-id",
                "-100",
            ],
        )
        assert result.exit_code != 0


class TestCronRemove:
    def test_remove_existing(self) -> None:
        runner = CliRunner()
        runner.invoke(
            cron,
            [
                "add",
                "--kind",
                "daily_summary",
                "--cron",
                "0 9 * * *",
                "--provider",
                "telegram",
                "--chat-id",
                "-100",
            ],
        )
        # Read the task ID from the store
        from app.scheduler.store import list_tasks

        tasks = list_tasks()
        task_id = tasks[0].id

        result = runner.invoke(cron, ["remove", task_id])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()

    def test_remove_nonexistent(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cron, ["remove", "ghost"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower()


class TestCronLogs:
    def test_no_runs(self) -> None:
        runner = CliRunner()
        runner.invoke(
            cron,
            [
                "add",
                "--kind",
                "daily_summary",
                "--cron",
                "0 9 * * *",
                "--provider",
                "telegram",
                "--chat-id",
                "-100",
            ],
        )
        from app.scheduler.store import list_tasks

        tasks = list_tasks()
        task_id = tasks[0].id

        result = runner.invoke(cron, ["logs", task_id])
        assert result.exit_code == 0
        assert "No runs" in result.output
