"""Daily metric rollups for reports and the dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from factory.metrics.store import MetricsStore

MAX_DAYS = 90


def daily_rollups(db_path: str | Path, days: int = 14) -> list[dict[str, Any]]:
    """Aggregates for the last N days (bounded), oldest first."""
    store = MetricsStore(db_path)
    days = min(days, MAX_DAYS)
    today = datetime.now(UTC).date()
    return [
        store.day_aggregates((today - timedelta(days=offset)).isoformat())
        for offset in range(days - 1, -1, -1)
    ]


def daily_summary_text(db_path: str | Path) -> str:
    """Human-readable summary of today - used by the `report` routine action."""
    today = datetime.now(UTC).date().isoformat()
    agg = MetricsStore(db_path).day_aggregates(today)
    pass_rate = (
        f"{agg['gate_pass_rate'] * 100:.0f}%" if agg["gate_pass_rate"] is not None else "n/a"
    )
    mttr = (
        f"{agg['selfheal_mttr_seconds']:.0f}s"
        if agg["selfheal_mttr_seconds"] is not None else "n/a"
    )
    return (
        f"factory report {today}: "
        f"{agg['agent_runs']} agent runs ({agg['agents_used']} roles), "
        f"{agg['tokens']} tokens, ~${agg['cost_usd']:.2f}, "
        f"{agg['fixes']} fixes, {agg['commits']} commits, "
        f"{agg['prs_opened']} PRs opened, {agg['prs_merged']} merged, "
        f"{agg['conflicts_resolved']} conflicts resolved, "
        f"gate pass rate {pass_rate}, self-heal MTTR {mttr}"
    )
