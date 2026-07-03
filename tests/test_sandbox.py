"""Sandbox executor tests (subprocess mode; docker mode is config-equivalent)."""

import sys

import pytest

from factory.config import SandboxConfig
from factory.sandbox import SandboxError, SandboxExecutor


def make_executor(**overrides) -> SandboxExecutor:
    cfg = SandboxConfig(
        mode="subprocess",
        command_allowlist=["python", "git"],
        timeout_seconds=30,
        **overrides,
    )
    return SandboxExecutor(cfg)


def test_allowlisted_command_runs(tmp_path):
    executor = make_executor()
    result = executor.run([sys.executable, "-c", "print('hi')"], cwd=tmp_path)
    assert result.ok
    assert "hi" in result.stdout


def test_non_allowlisted_command_refused(tmp_path):
    executor = make_executor()
    with pytest.raises(SandboxError, match="not in sandbox.command_allowlist"):
        executor.run(["curl", "http://example.com"], cwd=tmp_path)


def test_timeout_enforced(tmp_path):
    executor = make_executor()
    result = executor.run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        timeout_seconds=1,
    )
    assert result.timed_out
    assert not result.ok


def test_missing_cwd_refused(tmp_path):
    executor = make_executor()
    with pytest.raises(SandboxError, match="cwd does not exist"):
        executor.run(["python", "-V"], cwd=tmp_path / "nope")


def test_empty_command_refused(tmp_path):
    executor = make_executor()
    with pytest.raises(SandboxError, match="empty command"):
        executor.run([], cwd=tmp_path)


def test_docker_mode_requires_daemon(monkeypatch):
    import factory.sandbox.executor as mod

    monkeypatch.setattr(mod, "docker_available", lambda: False)
    with pytest.raises(SandboxError, match="docker daemon is unavailable"):
        SandboxExecutor(SandboxConfig(mode="docker"))


def test_auto_falls_back_to_subprocess(monkeypatch):
    import factory.sandbox.executor as mod

    monkeypatch.setattr(mod, "docker_available", lambda: False)
    executor = SandboxExecutor(SandboxConfig(mode="auto"))
    assert executor.mode == "subprocess"
