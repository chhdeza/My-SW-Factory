"""Persistent orchestrator state (SQLite) with crash-resume support.

Tasks, work units, and worktree registrations live in ``.factory/factory.db``.
On startup, ``reconcile()`` detects work interrupted by a crash and either
fails it forward or requeues it, and reports orphaned worktrees so the
integrator can prune them.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

DB_TIMEOUT_SECONDS = 5.0


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    CODING = "coding"
    INTEGRATING = "integrating"
    GATING = "gating"
    HEALING = "healing"
    PR_OPEN = "pr_open"
    MERGED = "merged"
    BLOCKED = "blocked"
    FAILED = "failed"
    DONE = "done"

    @property
    def terminal(self) -> bool:
        return self in (TaskStatus.MERGED, TaskStatus.FAILED, TaskStatus.DONE)


class UnitStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CODED = "coded"
    INTEGRATED = "integrated"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    title: str
    description: str
    source: str = "cli"          # cli | issue | webhook | selfheal | routine
    status: TaskStatus = TaskStatus.PENDING
    branch: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class WorkUnit:
    id: str
    task_id: str
    title: str
    description: str
    owned_files: list[str] = field(default_factory=list)
    branch: str = ""
    worktree_path: str = ""
    status: UnitStatus = UnitStatus.PENDING
    error: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'cli',
    status TEXT NOT NULL DEFAULT 'pending',
    branch TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    contract TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_units (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    owned_files TEXT NOT NULL DEFAULT '[]',
    branch TEXT NOT NULL DEFAULT '',
    worktree_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_units_task ON work_units(task_id);
"""


class StateStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=DB_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- tasks ------------------------------------------------------------

    def create_task(self, title: str, description: str, source: str = "cli") -> Task:
        task = Task(
            id=new_id("task"),
            title=title,
            description=description,
            source=source,
            created_at=_now(),
            updated_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tasks (id, title, description, source, status, branch, error,"
                " contract, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    task.id, task.title, task.description, task.source,
                    task.status.value, task.branch, task.error, "",
                    task.created_at, task.updated_at,
                ),
            )
        return task

    def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        branch: str | None = None,
        error: str | None = None,
        contract: str | None = None,
    ) -> None:
        sets, params = ["updated_at = ?"], [_now()]
        if status is not None:
            sets.append("status = ?")
            params.append(status.value)
        if branch is not None:
            sets.append("branch = ?")
            params.append(branch)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if contract is not None:
            sets.append("contract = ?")
            params.append(contract)
        params.append(task_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)

    def get_task(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None

    def get_task_contract(self, task_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT contract FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row["contract"] if row else ""

    def list_tasks(self, *, active_only: bool = False, limit: int = 100) -> list[Task]:
        # Bounded query (max 100 rows) - dashboard and CLI list views only.
        limit = min(limit, 100)
        query = "SELECT * FROM tasks"
        if active_only:
            terminal = tuple(s.value for s in TaskStatus if s.terminal)
            query += f" WHERE status NOT IN ({','.join('?' * len(terminal))})"
            params: tuple[Any, ...] = (*terminal, limit)
        else:
            params = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_task_from_row(r) for r in rows]

    # -- work units --------------------------------------------------------

    def create_unit(
        self, task_id: str, title: str, description: str, owned_files: list[str]
    ) -> WorkUnit:
        unit = WorkUnit(
            id=new_id("unit"),
            task_id=task_id,
            title=title,
            description=description,
            owned_files=owned_files,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO work_units (id, task_id, title, description, owned_files,"
                " branch, worktree_path, status, error, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    unit.id, unit.task_id, unit.title, unit.description,
                    json.dumps(unit.owned_files), unit.branch, unit.worktree_path,
                    unit.status.value, unit.error, _now(),
                ),
            )
        return unit

    def update_unit(
        self,
        unit_id: str,
        *,
        status: UnitStatus | None = None,
        branch: str | None = None,
        worktree_path: str | None = None,
        error: str | None = None,
    ) -> None:
        sets, params = ["updated_at = ?"], [_now()]
        if status is not None:
            sets.append("status = ?")
            params.append(status.value)
        if branch is not None:
            sets.append("branch = ?")
            params.append(branch)
        if worktree_path is not None:
            sets.append("worktree_path = ?")
            params.append(worktree_path)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(unit_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE work_units SET {', '.join(sets)} WHERE id = ?", params)

    def units_for_task(self, task_id: str) -> list[WorkUnit]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM work_units WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        return [_unit_from_row(r) for r in rows]

    # -- crash-resume -------------------------------------------------------

    def reconcile(self) -> list[Task]:
        """Recover from a crash: requeue interrupted work, return resumable tasks.

        Units left RUNNING by a dead process are reset to PENDING (their
        worktree may be stale; the integrator recreates it). Tasks stuck in a
        non-terminal state are returned so the caller can resume or block them.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE work_units SET status = ?, updated_at = ? WHERE status = ?",
                (UnitStatus.PENDING.value, _now(), UnitStatus.RUNNING.value),
            )
        return self.list_tasks(active_only=True)

    def registered_worktrees(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT worktree_path FROM work_units WHERE worktree_path != ''"
            ).fetchall()
        return {r["worktree_path"] for r in rows}


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        source=row["source"],
        status=TaskStatus(row["status"]),
        branch=row["branch"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _unit_from_row(row: sqlite3.Row) -> WorkUnit:
    return WorkUnit(
        id=row["id"],
        task_id=row["task_id"],
        title=row["title"],
        description=row["description"],
        owned_files=json.loads(row["owned_files"]),
        branch=row["branch"],
        worktree_path=row["worktree_path"],
        status=UnitStatus(row["status"]),
        error=row["error"],
    )
