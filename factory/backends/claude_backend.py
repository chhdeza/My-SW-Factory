"""Claude Agent SDK backend (``pip install software-factory[claude]``)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from factory.backends.base import BackendError, RunResult, RunStatus, Usage

logger = logging.getLogger(__name__)


class ClaudeBackend:
    name = "claude"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            raise BackendError("ANTHROPIC_API_KEY is not set (see .env.example)")
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as exc:
            raise BackendError(
                "claude-agent-sdk is not installed. "
                "Install with: pip install 'software-factory[claude]'"
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
        try:
            result = asyncio.run(
                asyncio.wait_for(
                    self._query(prompt, cwd, model, mcp_servers),
                    timeout=timeout_seconds,
                )
            )
        except TimeoutError:
            logger.error(
                "claude run timed out",
                extra={"operation": "agent_run", "provider": self.name, "model": model},
            )
            return RunResult(
                status=RunStatus.TIMEOUT,
                provider=self.name,
                model=model,
                error=f"run exceeded {timeout_seconds}s",
                duration_seconds=time.monotonic() - started,
            )
        except (ConnectionError, OSError) as err:
            return RunResult(
                status=RunStatus.STARTUP_FAILED,
                provider=self.name,
                model=model,
                error=str(err),
                retryable=True,
                duration_seconds=time.monotonic() - started,
            )
        result.duration_seconds = time.monotonic() - started
        return result

    async def _query(
        self, prompt: str, cwd: str, model: str, mcp_servers: dict[str, Any] | None
    ) -> RunResult:
        from claude_agent_sdk import ClaudeAgentOptions, query

        options = ClaudeAgentOptions(
            model=model,
            cwd=cwd,
            permission_mode="acceptEdits",
            mcp_servers=mcp_servers or {},
        )
        os.environ.setdefault("ANTHROPIC_API_KEY", self._api_key)

        output_parts: list[str] = []
        usage = Usage()
        status = RunStatus.ERROR
        error = ""
        async for message in query(prompt=prompt, options=options):
            kind = type(message).__name__
            if kind == "AssistantMessage":
                for block in getattr(message, "content", []):
                    text = getattr(block, "text", None)
                    if text:
                        output_parts.append(text)
            elif kind == "ResultMessage":
                is_error = bool(getattr(message, "is_error", False))
                status = RunStatus.ERROR if is_error else RunStatus.FINISHED
                error = str(getattr(message, "result", "")) if is_error else ""
                usage = _extract_usage(message)

        return RunResult(
            status=status,
            output="\n".join(output_parts),
            usage=usage,
            provider=self.name,
            model=model,
            error=error,
        )


def _extract_usage(result_message: Any) -> Usage:
    raw = getattr(result_message, "usage", None) or {}
    if not isinstance(raw, dict):
        raw = {}
    return Usage(
        prompt_tokens=int(raw.get("input_tokens", 0) or 0),
        completion_tokens=int(raw.get("output_tokens", 0) or 0),
        estimated_cost_usd=float(getattr(result_message, "total_cost_usd", 0.0) or 0.0),
    )
