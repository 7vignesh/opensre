"""Tests for per-kind message builders."""

from __future__ import annotations

import pytest

from app.scheduler.tasks import build_message
from app.scheduler.types import Provider, ScheduledTask, TaskKind


class TestMessageBuilders:
    def test_daily_summary(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.DAILY_SUMMARY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
            window_hours=24,
        )
        msg = build_message(task)
        assert "Daily Reliability Summary" in msg
        assert "24h" in msg
        assert "OpenSRE" in msg

    def test_weekly_audit(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.WEEKLY_AUDIT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
            window_hours=168,
        )
        msg = build_message(task)
        assert "Weekly Alert Audit" in msg
        assert "168h" in msg

    def test_synthetic_run(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.SYNTHETIC_RUN,
            cron="0 6 * * *",
            provider=Provider.DISCORD,
            chat_id="123",
        )
        msg = build_message(task)
        assert "Synthetic Test Summary" in msg

    def test_incident_window_replay_pipeline_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.INCIDENT_WINDOW_REPLAY,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        import app.scheduler.tasks as tasks_mod

        def _mock_build_replay(_t: ScheduledTask) -> str:
            raise RuntimeError("Pipeline failed")

        monkeypatch.setattr(tasks_mod, "_build_incident_window_replay", _mock_build_replay)

        with pytest.raises(RuntimeError, match="Pipeline failed"):
            tasks_mod._build_incident_window_replay(task)

    def test_custom_investigation_pipeline_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        import app.scheduler.tasks as tasks_mod

        def _mock_build_custom(_t: ScheduledTask) -> str:
            raise RuntimeError("Custom investigation failed")

        monkeypatch.setattr(tasks_mod, "_build_custom_investigation", _mock_build_custom)

        with pytest.raises(RuntimeError, match="Custom investigation failed"):
            tasks_mod._build_custom_investigation(task)

    def test_custom_investigation_strips_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify credential keys are not passed to the investigation pipeline."""
        task = ScheduledTask(
            kind=TaskKind.CUSTOM_INVESTIGATION,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
            params={"bot_token": "secret123", "custom_param": "safe_value"},
        )

        captured_payload: dict[str, object] = {}

        def _mock_run_investigation(payload: object, **_kwargs: object) -> dict[str, str]:
            captured_payload.update(payload)  # type: ignore[arg-type]
            return {"report": "test report"}

        monkeypatch.setattr(
            "app.pipeline.runners.run_investigation",
            _mock_run_investigation,
        )

        # Call the builder directly — it imports run_investigation lazily
        import app.scheduler.tasks as tasks_mod

        tasks_mod._build_custom_investigation(task)
        assert "bot_token" not in captured_payload
        assert captured_payload.get("custom_param") == "safe_value"
