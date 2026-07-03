"""SQLite metrics store: agent runs, factory events, routine runs.

Lives in the same ``.factory/factory.db`` as orchestrator state so one file
holds all runtime data.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_TIMEOUT_SECONDS = 5.0
MAX_PAGE = 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    trace_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_day ON agent_runs(day);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_day_kind ON events(day, kind);

CREATE TABLE IF NOT EXISTS routine_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    name TEXT NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    duration_seconds REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_routine_runs_name ON routine_runs(name);
"""

# Event kinds the rollup understands.
EVENT_KINDS = (
    "task_created", "commit", "pr_opened", "pr_merged", "fix_applied",
    "conflict_resolved", "gate_passed", "gate_failed", "deploy_approved",
    "deploy_executed", "heal_started", "heal_succeeded",
)


def _now() -> tuple[str, str]:
    now = datetime.now(UTC)
    return now.isoformat(timespec="seconds"), now.date().isoformat()


class MetricsStore:
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

    # -- writes ------------------------------------------------------------

    def record_agent_run(
        self,
        *,
        role: str,
        status: str,
        provider: str = "",
        model: str = "",
        task_id: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_seconds: float = 0.0,
        trace_id: str = "",
    ) -> None:
        ts, day = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_runs (ts, day, task_id, role, provider, model, status,"
                " prompt_tokens, completion_tokens, cost_usd, duration_seconds, trace_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, day, task_id, role, provider, model, status, prompt_tokens,
                 completion_tokens, cost_usd, duration_seconds, trace_id),
            )

    def record_event(self, kind: str, task_id: str = "", detail: str = "") -> None:
        ts, day = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (ts, day, kind, task_id, detail) VALUES (?,?,?,?,?)",
                (ts, day, kind, task_id, detail[:1000]),
            )

    def record_routine_run(
        self, name: str, ok: bool, detail: str, duration_seconds: float
    ) -> None:
        ts, day = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO routine_runs (ts, day, name, ok, detail, duration_seconds)"
                " VALUES (?,?,?,?,?,?)",
                (ts, day, name, int(ok), detail[:1000], duration_seconds),
            )

    # -- bounded reads (dashboard) ----------------------------------------------

    def recent_agent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?",
                (min(limit, MAX_PAGE),),
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_routine_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM routine_runs ORDER BY id DESC LIMIT ?",
                (min(limit, MAX_PAGE),),
            ).fetchall()
        return [dict(r) for r in rows]

    def events_for_day(self, day: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE day = ? ORDER BY id LIMIT ?",
                (day, MAX_PAGE * 10),
            ).fetchall()
        return [dict(r) for r in rows]

    def day_aggregates(self, day: str) -> dict[str, Any]:
        with self._connect() as conn:
            runs = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(prompt_tokens + completion_tokens),0)"
                " AS tokens, COALESCE(SUM(cost_usd),0) AS cost,"
                " COUNT(DISTINCT role) AS roles"
                " FROM agent_runs WHERE day = ?",
                (day,),
            ).fetchone()
            events = conn.execute(
                "SELECT kind, COUNT(*) AS n FROM events WHERE day = ? GROUP BY kind",
                (day,),
            ).fetchall()
            heal = conn.execute(
                "SELECT AVG(duration_seconds) AS mttr FROM agent_runs"
                " WHERE day = ? AND role = 'fixer' AND status = 'finished'",
                (day,),
            ).fetchone()
        event_counts = {row["kind"]: row["n"] for row in events}
        gates_total = event_counts.get("gate_passed", 0) + event_counts.get("gate_failed", 0)
        return {
            "day": day,
            "agent_runs": runs["n"],
            "agents_used": runs["roles"],
            "tokens": runs["tokens"],
            "cost_usd": round(runs["cost"], 4),
            "fixes": event_counts.get("fix_applied", 0),
            "commits": event_counts.get("commit", 0),
            "prs_opened": event_counts.get("pr_opened", 0),
            "prs_merged": event_counts.get("pr_merged", 0),
            "conflicts_resolved": event_counts.get("conflict_resolved", 0),
            "gate_pass_rate": (
                round(event_counts.get("gate_passed", 0) / gates_total, 3)
                if gates_total else None
            ),
            "selfheal_mttr_seconds": round(heal["mttr"], 1) if heal["mttr"] else None,
        }
