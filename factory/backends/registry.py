"""Per-role backend resolution from factory.yaml ``models:``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from factory.backends.base import AgentBackend, RunResult
from factory.config import FactoryConfig, Provider

logger = logging.getLogger(__name__)

# Called after every agent run: (role, prompt, result) -> None.
Observer = Callable[[str, str, RunResult], None]


class BackendRegistry:
    """Resolves an agent role to a (backend, model) pair and runs prompts.

    Backends are constructed lazily and cached, so an unconfigured provider
    only fails if a role actually requires it.
    """

    def __init__(self, config: FactoryConfig) -> None:
        self._config = config
        self._backends: dict[Provider, AgentBackend] = {}
        self.observer: Observer | None = None

    def register(self, provider: Provider, backend: AgentBackend) -> None:
        """Register a pre-built backend (used by tests and custom providers)."""
        self._backends[provider] = backend

    def backend_for(self, provider: Provider) -> AgentBackend:
        if provider not in self._backends:
            if provider is Provider.CURSOR:
                from factory.backends.cursor_backend import CursorBackend

                self._backends[provider] = CursorBackend()
            else:
                from factory.backends.claude_backend import ClaudeBackend

                self._backends[provider] = ClaudeBackend()
        return self._backends[provider]

    def run(
        self,
        role: str,
        prompt: str,
        *,
        cwd: str,
        timeout_seconds: int | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> RunResult:
        """Run one prompt as the given role, using its configured provider/model."""
        spec = self._config.model_for_role(role)
        backend = self.backend_for(spec.provider)
        timeout = timeout_seconds or self._config.sandbox.timeout_seconds
        logger.info(
            "agent run start",
            extra={
                "operation": "agent_run",
                "role": role,
                "provider": spec.provider.value,
                "model": spec.model,
            },
        )
        result = backend.run(
            prompt=prompt,
            cwd=cwd,
            model=spec.model,
            timeout_seconds=timeout,
            mcp_servers=mcp_servers,
        )
        logger.info(
            "agent run done",
            extra={
                "operation": "agent_run",
                "role": role,
                "status": result.status.value,
                "tokens": result.usage.total_tokens,
            },
        )
        if self.observer is not None:
            self.observer(role, prompt, result)
        return result
