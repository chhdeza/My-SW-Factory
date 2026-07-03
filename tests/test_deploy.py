"""Deploy gate tests: nothing executes without human approval."""

import pytest

from factory.config import FactoryConfig
from factory.deploy.gate import DeployGate
from factory.deploy.runner import DeployError, DeployRunner


def make_gate(tmp_path) -> DeployGate:
    return DeployGate(tmp_path, FactoryConfig())


def test_request_then_approve_then_execute(tmp_path):
    gate = make_gate(tmp_path)
    gate.request("v1.2.3")
    status = gate.status()
    assert status.pending == "v1.2.3"
    assert not status.approved

    gate.approve(approver="alice")
    assert gate.status().approved

    runner = DeployRunner(tmp_path, FactoryConfig())  # hook: noop
    result = runner.execute()
    assert "noop deploy of v1.2.3" in result
    assert gate.status().pending is None
    assert gate.status().history[-1]["outcome"] == "executed"


def test_execute_refused_without_approval(tmp_path):
    gate = make_gate(tmp_path)
    gate.request("v1.0.0")
    runner = DeployRunner(tmp_path, FactoryConfig())
    with pytest.raises(DeployError, match="approval is mandatory"):
        runner.execute()
    assert gate.status().pending == "v1.0.0"  # request survives the refusal


def test_execute_refused_with_nothing_pending(tmp_path):
    runner = DeployRunner(tmp_path, FactoryConfig())
    with pytest.raises(DeployError):
        runner.execute()


def test_reject_clears_pending(tmp_path):
    gate = make_gate(tmp_path)
    gate.request("v2.0.0")
    gate.reject(approver="bob")
    status = gate.status()
    assert status.pending is None
    assert status.history[-1]["outcome"] == "rejected"


def test_second_request_blocked_while_pending(tmp_path):
    gate = make_gate(tmp_path)
    gate.request("a")
    assert "already pending" in gate.request("b")


def test_unknown_hook_refused(tmp_path):
    cfg = FactoryConfig()
    cfg.deploy.hook = "teleport"
    gate = DeployGate(tmp_path, cfg)
    gate.request("x")
    gate.approve(approver="a")
    with pytest.raises(DeployError, match="unknown deploy hook"):
        DeployRunner(tmp_path, cfg).execute()


def test_custom_dotted_hook(tmp_path, monkeypatch):
    cfg = FactoryConfig()
    cfg.deploy.hook = "tests.test_deploy:fake_hook"
    gate = DeployGate(tmp_path, cfg)
    gate.request("abc")
    gate.approve(approver="a")
    assert DeployRunner(tmp_path, cfg).execute() == "deployed abc"


def fake_hook(ref: str, config) -> str:
    return f"deployed {ref}"
