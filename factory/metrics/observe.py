"""Attach metrics + trace capture to a backend registry."""

from __future__ import annotations

from pathlib import Path

from factory.backends.base import RunResult
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig
from factory.metrics.store import MetricsStore
from factory.metrics.traces import TraceWriter
from factory.metrics.usage import estimate_cost_usd


def attach_observability(
    registry: BackendRegistry, repo_root: Path, config: FactoryConfig
) -> tuple[MetricsStore, TraceWriter]:
    """Record every agent run to metrics and write a redacted trace."""
    metrics = MetricsStore(repo_root / ".factory" / "factory.db")
    tracer = TraceWriter(repo_root / config.tracing.dir, config.tracing)

    def observer(role: str, prompt: str, result: RunResult) -> None:
        trace_id = tracer.write(
            role=role,
            prompt=prompt,
            output=result.output,
            provider=result.provider,
            model=result.model,
            status=result.status.value,
            events=result.raw_events,
        )
        metrics.record_agent_run(
            role=role,
            status=result.status.value,
            provider=result.provider,
            model=result.model,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            cost_usd=estimate_cost_usd(result.model, result.usage),
            duration_seconds=result.duration_seconds,
            trace_id=trace_id,
        )

    registry.observer = observer
    return metrics, tracer
