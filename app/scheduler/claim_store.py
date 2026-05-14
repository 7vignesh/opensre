"""SQLite-backed execution claims and run history.

Provides single-writer semantics: when multiple scheduler instances race
to execute the same task at the same fire time, only one wins the claim.
The loser sees an IntegrityError and skips execution.
"""

from __future__ import annotations

import logging
import os
import platform
import sqlite3
from pathlib import Path

from app.constants import OPENSRE_HOME_DIR
from app.scheduler.types import TaskRun, TaskStatus

logger = logging.getLogger(__name__)

SCHEDULER_DB_PATH: Path = OPENSRE_HOME_DIR / "scheduler.db"

_INSTANCE_ID = f"{platform.node()}:{os.getpid()}"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS execution_claims (
    task_id TEXT NOT NULL,
    fire_time TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, fire_time)
);

CREATE TABLE IF NOT EXISTS task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    posted_message_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_task_runs_task_id ON task_runs(task_id);
"""


def _connect() -> sqlite3.Connection:
    SCHEDULER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SCHEDULER_DB_PATH), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def try_claim(task_id: str, fire_time: str) -> bool:
    """Attempt to claim a task execution slot.

    Returns True if this instance won the claim, False if another
    instance already claimed it. Uses INSERT OR IGNORE + rowcount
    to atomically detect conflicts.
    """
    conn = _connect()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO execution_claims (task_id, fire_time, instance_id) VALUES (?, ?, ?)",
            (task_id, fire_time, _INSTANCE_ID),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def save_run(run: TaskRun) -> None:
    """Persist a task run record to SQLite."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO task_runs (task_id, started_at, finished_at, status, posted_message_id, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run.task_id,
                run.started_at,
                run.finished_at or "",
                run.status.value,
                run.posted_message_id,
                run.error,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_runs(task_id: str, limit: int = 10) -> list[TaskRun]:
    """Return recent runs for a task, newest first."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "SELECT task_id, started_at, finished_at, status, posted_message_id, error "
            "FROM task_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        )
        runs = []
        for row in cursor.fetchall():
            runs.append(
                TaskRun(
                    task_id=row[0],
                    started_at=row[1],
                    finished_at=row[2] or None,
                    status=TaskStatus(row[3]),
                    posted_message_id=row[4],
                    error=row[5],
                )
            )
        return runs
    finally:
        conn.close()


def delete_runs(task_id: str) -> None:
    """Remove all run history and claims for a task."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM task_runs WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM execution_claims WHERE task_id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()
