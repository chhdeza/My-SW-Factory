"""Agent backend protocol and normalized result types.

Every provider (Cursor, Claude, ...) implements ``AgentBackend`` and returns the
same ``RunResult`` shape, so the orchestrator, gates, and metrics never care
which SDK did the work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class RunStatus(StrEnum):
    FINISHED = "finished"
    ERROR = "error"           # run executed and failed
    STARTUP_FAILED = "startup_failed"  # run never executed (auth/config/network)
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class BackendError(Exception):
    """Raised when a backend cannot run at all (missing SDK, missing key)."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class RunResult:
    status: RunStatus
    output: str = ""
    usage: Usage = field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    agent_id: str = ""
    run_id: str = ""
    error: str = ""
    retryable: bool = False
    duration_seconds: float = 0.0
    raw_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.FINISHED


@runtime_checkable
class AgentBackend(Protocol):
    """A provider capable of executing one agent run."""

    name: str

    def run(
        self,
        *,
        prompt: str,
        cwd: str,
        model: str,
        timeout_seconds: int = 900,
        mcp_servers: dict[str, Any] | None = None,
    ) -> RunResult:
        """Execute one agent run in ``cwd`` and return a normalized result.

        Must never raise for run-level failures; those are reported via
        ``RunResult.status``. Raises ``BackendError`` only when the backend is
        unusable (SDK not installed, credentials missing).
        """
        ...
