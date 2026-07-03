"""Metrics store, usage estimation, rollups, and trace tests."""

from datetime import UTC, datetime

from factory.backends.base import Usage
from factory.config import TracingConfig
from factory.metrics.rollup import daily_rollups, daily_summary_text
from factory.metrics.store import MetricsStore
from factory.metrics.traces import TraceWriter, redact
from factory.metrics.usage import estimate_cost_usd, price_for_model


def today() -> str:
    return datetime.now(UTC).date().isoformat()


# -- store + rollups -----------------------------------------------------------


def test_agent_run_and_day_aggregates(tmp_path):
    store = MetricsStore(tmp_path / "db.sqlite")
    store.record_agent_run(role="coder", status="finished", provider="cursor",
                           model="composer-2.5", prompt_tokens=1000,
                           completion_tokens=500, cost_usd=0.05, duration_seconds=12)
    store.record_agent_run(role="fixer", status="finished", duration_seconds=30)
    store.record_event("fix_applied", "task-1")
    store.record_event("pr_opened", "task-1")
    store.record_event("gate_passed", "task-1")
    store.record_event("gate_failed", "task-1")

    agg = store.day_aggregates(today())

    assert agg["agent_runs"] == 2
    assert agg["agents_used"] == 2
    assert agg["tokens"] == 1500
    assert agg["fixes"] == 1
    assert agg["prs_opened"] == 1
    assert agg["gate_pass_rate"] == 0.5
    assert agg["selfheal_mttr_seconds"] == 30


def test_routine_run_history(tmp_path):
    store = MetricsStore(tmp_path / "db.sqlite")
    store.record_routine_run("daily_log_review", True, "ok", 4.2)
    runs = store.recent_routine_runs()
    assert runs[0]["name"] == "daily_log_review"
    assert runs[0]["ok"] == 1


def test_daily_rollups_and_summary(tmp_path):
    db = tmp_path / "db.sqlite"
    store = MetricsStore(db)
    store.record_agent_run(role="coder", status="finished", prompt_tokens=10,
                           completion_tokens=5)
    days = daily_rollups(db, days=3)
    assert len(days) == 3
    assert days[-1]["day"] == today()
    assert "agent runs" in daily_summary_text(db)


# -- usage/cost --------------------------------------------------------------


def test_price_prefix_matching():
    assert price_for_model("composer-2.5") == (2.0, 10.0)
    assert price_for_model("claude-sonnet-4") == (3.0, 15.0)
    assert price_for_model("mystery-model") == (3.0, 15.0)  # default


def test_reported_cost_wins():
    usage = Usage(prompt_tokens=1_000_000, estimated_cost_usd=1.23)
    assert estimate_cost_usd("composer-2.5", usage) == 1.23


def test_estimated_cost_from_table():
    usage = Usage(prompt_tokens=1_000_000, completion_tokens=100_000)
    assert estimate_cost_usd("composer-2.5", usage) == 2.0 + 1.0


# -- traces ------------------------------------------------------------------


def test_redaction_patterns():
    text = (
        "key=sk-abc123def456ghi789 gh token ghp_ABCDEFGHIJKLMNOPQRST123456 "
        "AWS AKIAIOSFODNN7EXAMPLE and MY_API_KEY=supersecret plus "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6"
    )
    result = redact(text)
    assert "sk-abc123" not in result
    assert "ghp_" not in result
    assert "AKIAIOSFODNN7EXAMPLE" not in result
    assert "supersecret" not in result
    assert "MY_API_KEY=[REDACTED]" in result
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6" not in result


def test_trace_write_read_redacted(tmp_path):
    writer = TraceWriter(tmp_path, TracingConfig(retention_days=30))
    trace_id = writer.write(role="coder", prompt="use CURSOR_API_KEY=cursor_abc123def456",
                            output="done", provider="cursor", model="m", status="finished")
    record = writer.read(trace_id)
    assert record is not None
    assert "cursor_abc123def456" not in record["prompt"]
    assert writer.list_traces()[0]["trace_id"] == trace_id


def test_trace_read_rejects_bad_ids(tmp_path):
    writer = TraceWriter(tmp_path, TracingConfig())
    assert writer.read("../../etc/passwd") is None
    assert writer.read("trace-zzz") is None


def test_trace_disabled_writes_nothing(tmp_path):
    writer = TraceWriter(tmp_path, TracingConfig(enabled=False))
    assert writer.write(role="r", prompt="p", output="o") == ""
    assert writer.list_traces() == []


def test_retention_removes_old_days(tmp_path):
    writer = TraceWriter(tmp_path, TracingConfig(retention_days=7))
    old_dir = tmp_path / "2020-01-01"
    old_dir.mkdir(parents=True)
    (old_dir / "trace-abcdefabcdef.json").write_text("{}", encoding="utf-8")
    assert writer.apply_retention() == 1
    assert not old_dir.exists()
