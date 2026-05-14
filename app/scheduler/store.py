"""JSON-backed persistence for scheduled task definitions.

Run history lives in SQLite (see claim_store.py). This module handles
only the task configuration CRUD.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from app.constants import OPENSRE_HOME_DIR
from app.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)

SCHEDULER_STORE_PATH: Path = OPENSRE_HOME_DIR / "scheduler.json"
_LOCK_TIMEOUT_SECONDS = 5.0


def _lock_path() -> Path:
    return SCHEDULER_STORE_PATH.with_suffix(".lock")


def _load_raw() -> dict[str, Any]:
    if not SCHEDULER_STORE_PATH.exists():
        return {"tasks": []}
    try:
        data = json.loads(SCHEDULER_STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to read scheduler store at %s", SCHEDULER_STORE_PATH)
        return {"tasks": []}
    if not isinstance(data, dict):
        return {"tasks": []}
    return data


def _save(data: dict[str, Any]) -> None:
    SCHEDULER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULER_STORE_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def list_tasks() -> list[ScheduledTask]:
    """Return all scheduled tasks."""
    data = _load_raw()
    tasks = []
    for raw in data.get("tasks", []):
        try:
            tasks.append(ScheduledTask.model_validate(raw))
        except Exception:
            logger.warning("Skipping invalid task record: %s", raw)
    return tasks


def get_task(task_id: str) -> ScheduledTask | None:
    """Return a task by ID, or None."""
    for task in list_tasks():
        if task.id == task_id:
            return task
    return None


def add_task(task: ScheduledTask) -> None:
    """Persist a new scheduled task."""
    SCHEDULER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(_lock_path()), timeout=_LOCK_TIMEOUT_SECONDS)
    try:
        with lock:
            data = _load_raw()
            tasks = data.get("tasks", [])
            tasks.append(task.model_dump(mode="json"))
            data["tasks"] = tasks
            _save(data)
    except Timeout:
        raise TimeoutError("Scheduler store locked") from None


def remove_task(task_id: str) -> bool:
    """Remove a task by ID. Returns True if found and removed."""
    from app.scheduler.claim_store import delete_runs

    lock = FileLock(str(_lock_path()), timeout=_LOCK_TIMEOUT_SECONDS)
    try:
        with lock:
            data = _load_raw()
            before = len(data.get("tasks", []))
            data["tasks"] = [t for t in data.get("tasks", []) if t.get("id") != task_id]
            changed = len(data["tasks"]) < before
            if changed:
                _save(data)
                delete_runs(task_id)
            return changed
    except Timeout:
        raise TimeoutError("Scheduler store locked") from None


def update_task(task: ScheduledTask) -> None:
    """Update an existing task in place."""
    lock = FileLock(str(_lock_path()), timeout=_LOCK_TIMEOUT_SECONDS)
    try:
        with lock:
            data = _load_raw()
            tasks = data.get("tasks", [])
            for i, t in enumerate(tasks):
                if t.get("id") == task.id:
                    tasks[i] = task.model_dump(mode="json")
                    break
            data["tasks"] = tasks
            _save(data)
    except Timeout:
        raise TimeoutError("Scheduler store locked") from None
