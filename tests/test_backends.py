"""Backend protocol, registry, and role prompt tests."""

from typing import Any

import pytest

from factory.agents import ROLES, compose_prompt, load_role_prompt
from factory.backends import AgentBackend, BackendRegistry, RunResult, RunStatus, Usage
from factory.config import FactoryConfig, ModelSpec, Provider


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        *,
        prompt: str,
        cwd: str,
        model: str,
        timeout_seconds: int = 900,
        mcp_servers: dict[str, Any] | None = None,
    ) -> RunResult:
        self.calls.append({"prompt": prompt, "cwd": cwd, "model": model})
        return RunResult(
            status=RunStatus.FINISHED,
            output="done",
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            provider=self.name,
            model=model,
        )


def test_fake_backend_satisfies_protocol():
    assert isinstance(FakeBackend(), AgentBackend)


def test_registry_resolves_role_model_and_runs():
    cfg = FactoryConfig(
        models={
            "default": ModelSpec(provider=Provider.CURSOR, model="m-default"),
            "security": ModelSpec(provider=Provider.CLAUDE, model="m-sec"),
        }
    )
    registry = BackendRegistry(cfg)
    fake = FakeBackend()
    registry.register(Provider.CURSOR, fake)
    registry.register(Provider.CLAUDE, fake)

    result = registry.run("coder", "do it", cwd=".")
    assert result.ok
    assert fake.calls[-1]["model"] == "m-default"

    registry.run("security", "scan it", cwd=".")
    assert fake.calls[-1]["model"] == "m-sec"


def test_usage_totals():
    usage = Usage(prompt_tokens=100, completion_tokens=50)
    assert usage.total_tokens == 150


def test_all_role_prompts_exist_and_load():
    for role in ROLES:
        prompt = load_role_prompt(role)
        assert prompt.startswith("# Role:"), role


def test_unknown_role_rejected():
    with pytest.raises(ValueError, match="unknown agent role"):
        load_role_prompt("nonexistent")


def test_compose_prompt_includes_context_and_task():
    full = compose_prompt("coder", "implement X", context="contract: Y")
    assert "# Role: Coder" in full
    assert "contract: Y" in full
    assert "implement X" in full
