"""Cursor SDK backend (``pip install software-factory[cursor]``)."""

from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from typing import Any

from factory.backends.base import BackendError, RunResult, RunStatus, Usage

logger = logging.getLogger(__name__)


class CursorBackend:
    name = "cursor"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("CURSOR_API_KEY", "")
        if not self._api_key:
            raise BackendError("CURSOR_API_KEY is not set (see .env.example)")
        try:
            import cursor_sdk  # noqa: F401
        except ImportError as exc:
            raise BackendError(
                "cursor-sdk is not installed. Install with: pip install 'software-factory[cursor]'"
            ) from exc

    def run(
        self,
        *,
        prompt: str,
        cwd: str,
        model: str,
        timeout_seconds: int = 900,
        mcp_servers: dict[str, Any] | None = None,
    ) -> RunResult:
        started = time.monotonic()
        # Agent.prompt is a blocking one-shot; enforce the sandbox timeout by
        # running it on a worker thread (memory cost: one thread per run).
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._prompt, prompt, cwd, model, mcp_servers)
            try:
                result = future.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                logger.error(
                    "cursor run timed out",
                    extra={"operation": "agent_run", "provider": self.name, "model": model},
                )
                return RunResult(
                    status=RunStatus.TIMEOUT,
                    provider=self.name,
                    model=model,
                    error=f"run exceeded {timeout_seconds}s",
                    duration_seconds=time.monotonic() - started,
                )
        result.duration_seconds = time.monotonic() - started
        return result

    def _prompt(
        self, prompt: str, cwd: str, model: str, mcp_servers: dict[str, Any] | None
    ) -> RunResult:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

        options = AgentOptions(
            api_key=self._api_key,
            model=model,
            local=LocalAgentOptions(cwd=cwd),
        )
        if mcp_servers:
            options.mcp_servers = mcp_servers
        try:
            sdk_result = Agent.prompt(prompt, options)
        except CursorAgentError as err:
            # Startup failure: the run never executed (auth, config, network).
            return RunResult(
                status=RunStatus.STARTUP_FAILED,
                provider=self.name,
                model=model,
                error=str(getattr(err, "message", err)),
                retryable=bool(getattr(err, "is_retryable", False)),
            )

        status = (
            RunStatus.FINISHED
            if getattr(sdk_result, "status", "") == "finished"
            else RunStatus.ERROR
        )
        return RunResult(
            status=status,
            output=str(getattr(sdk_result, "result", "") or ""),
            usage=_extract_usage(sdk_result),
            provider=self.name,
            model=model,
            agent_id=str(getattr(sdk_result, "agent_id", "") or ""),
            run_id=str(getattr(sdk_result, "id", "") or ""),
            error=(
                ""
                if status is RunStatus.FINISHED
                else f"run {getattr(sdk_result, 'id', '')} failed"
            ),
        )


def _extract_usage(sdk_result: Any) -> Usage:
    """Best-effort usage extraction; SDK result shapes evolve."""
    usage = getattr(sdk_result, "usage", None)
    if usage is None:
        return Usage()
    return Usage(
        prompt_tokens=int(
            getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
        ),
        completion_tokens=int(
            getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0
        ),
        estimated_cost_usd=float(getattr(usage, "cost_usd", 0.0) or 0.0),
    )
