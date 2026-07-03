"""Self-heal loop guardrails: signature dedupe, attempt caps, backoff.

Every healing attempt is keyed by a stable failure signature. A signature that
keeps failing is retried at most ``max_fix_attempts`` times with exponential
backoff, then permanently escalated to a human. This is what prevents runaway
healing loops (and runaway token spend).
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB_TIMEOUT_SECONDS = 5.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS heal_attempts (
    signature TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt TEXT NOT NULL,
    next_allowed TEXT NOT NULL
);
"""


def make_signature(*parts: str) -> str:
    """Stable short signature for a failure (order-sensitive)."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


class HealLedger:
    def __init__(self, db_path: str | Path, *, backoff_base_seconds: int = 60) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._backoff_base = backoff_base_seconds
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=DB_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def attempts(self, signature: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM heal_attempts WHERE signature = ?", (signature,)
            ).fetchone()
        return int(row["attempts"]) if row else 0

    def allowed(self, signature: str, max_attempts: int) -> bool:
        """True when this signature may be healed again (under cap, past backoff)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts, next_allowed FROM heal_attempts WHERE signature = ?",
                (signature,),
            ).fetchone()
        if row is None:
            return True
        if int(row["attempts"]) >= max_attempts:
            return False
        return datetime.now(UTC) >= datetime.fromisoformat(row["next_allowed"])

    def record(self, signature: str) -> int:
        """Record one attempt; returns the new attempt count."""
        now = datetime.now(UTC)
        attempts = self.attempts(signature) + 1
        # Exponential backoff: base * 2^(attempts-1).
        delay = timedelta(seconds=self._backoff_base * (2 ** (attempts - 1)))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO heal_attempts (signature, attempts, last_attempt, next_allowed)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(signature) DO UPDATE SET attempts = excluded.attempts,"
                " last_attempt = excluded.last_attempt, next_allowed = excluded.next_allowed",
                (signature, attempts, now.isoformat(), (now + delay).isoformat()),
            )
        return attempts
