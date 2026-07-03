"""Bootstrap (factory init) tests."""

import yaml

from factory.bootstrap import InitAnswers, write_config, write_env
from factory.config import load_config


def test_write_config_produces_valid_factory_yaml(tmp_path):
    answers = InitAnswers(provider="claude", default_model="claude-sonnet-latest",
                          max_cost_per_day=10.0, sandbox_mode="subprocess",
                          scheduler_runner="ci")
    path = write_config(answers, tmp_path)
    assert path.exists()

    cfg = load_config(tmp_path)  # must pass Pydantic validation
    assert cfg.provider.value == "claude"
    assert cfg.models["default"].model == "claude-sonnet-latest"
    assert cfg.budgets.max_cost_usd_per_day == 10.0
    assert cfg.sandbox.mode == "subprocess"
    assert cfg.scheduler.runner == "ci"


def test_write_config_preserves_existing_settings(tmp_path):
    (tmp_path / "factory.yaml").write_text(
        yaml.safe_dump({"merge": {"needs_human_label": "custom-label"}}),
        encoding="utf-8",
    )
    write_config(InitAnswers(), tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.merge.needs_human_label == "custom-label"  # untouched
    assert cfg.provider.value == "cursor"                  # applied


def test_write_env_only_provided_keys(tmp_path):
    env = write_env(InitAnswers(cursor_api_key="cur_123"), tmp_path)
    content = env.read_text(encoding="utf-8")
    assert "CURSOR_API_KEY=cur_123" in content
    assert "ANTHROPIC" not in content
    assert "GITHUB_TOKEN" not in content
