"""Config loading and validation tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory.config import FactoryConfig, ModelSpec, Provider, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shipped_factory_yaml_is_valid():
    cfg = load_config(REPO_ROOT)
    assert cfg.provider in (Provider.CURSOR, Provider.CLAUDE)
    assert cfg.budgets.max_concurrent_agents >= 1
    assert cfg.selfheal.max_fix_attempts >= 1
    assert "python" in cfg.gates.profiles


def test_missing_config_raises():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent-dir")


def test_model_for_role_falls_back_to_default():
    cfg = FactoryConfig(models={"default": ModelSpec(provider=Provider.CLAUDE, model="m1")})
    assert cfg.model_for_role("coder").model == "m1"
    assert cfg.model_for_role("coder").provider is Provider.CLAUDE


def test_invalid_cron_rejected():
    with pytest.raises(ValidationError):
        FactoryConfig.model_validate(
            {"schedules": {"bad": {"cron": "not a cron", "action": {"type": "report"}}}}
        )


def test_mcp_env_resolution(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret-value")
    cfg = FactoryConfig.model_validate(
        {"mcps": {"s": {"enabled": True, "env": {"TOKEN": "${MY_TOKEN}", "PLAIN": "x"}}}}
    )
    resolved = cfg.mcps["s"].resolved_env()
    assert resolved == {"TOKEN": "secret-value", "PLAIN": "x"}
