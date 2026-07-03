"""Interactive bootstrap (``factory init``): writes factory.yaml and .env.

Loads the shipped factory.yaml as the base so defaults stay in one place,
applies the operator's answers, and writes secrets to .env (never committed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import click
import yaml

logger = logging.getLogger(__name__)

TEMPLATE_CONFIG = Path(__file__).resolve().parent.parent / "factory.yaml"


@dataclass
class InitAnswers:
    provider: str = "cursor"
    topology: str = "in_repo"
    default_model: str = "composer-2.5"
    max_cost_per_day: float = 25.0
    max_concurrent_agents: int = 3
    sandbox_mode: str = "auto"
    scheduler_runner: str = "local"
    deploy_environment: str = "production"
    cursor_api_key: str = ""
    anthropic_api_key: str = ""
    github_token: str = ""
    mcps: dict = field(default_factory=dict)


def write_config(answers: InitAnswers, root: Path) -> Path:
    """Apply answers on top of the template config and write factory.yaml."""
    base_path = root / "factory.yaml"
    source = base_path if base_path.exists() else TEMPLATE_CONFIG
    config = (
        yaml.safe_load(source.read_text(encoding="utf-8")) if source.exists() else {}
    ) or {}

    config["provider"] = answers.provider
    config["topology"] = answers.topology
    config.setdefault("models", {})["default"] = {
        "provider": answers.provider,
        "model": answers.default_model,
    }
    config.setdefault("budgets", {})["max_cost_usd_per_day"] = answers.max_cost_per_day
    config["budgets"]["max_concurrent_agents"] = answers.max_concurrent_agents
    config.setdefault("sandbox", {})["mode"] = answers.sandbox_mode
    config.setdefault("scheduler", {})["runner"] = answers.scheduler_runner
    config.setdefault("deploy", {})["environment"] = answers.deploy_environment
    if answers.mcps:
        config["mcps"] = answers.mcps

    base_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return base_path


def write_env(answers: InitAnswers, root: Path) -> Path:
    """Write .env with only the keys that were provided."""
    lines = ["# Written by `factory init`. Never commit this file."]
    if answers.cursor_api_key:
        lines.append(f"CURSOR_API_KEY={answers.cursor_api_key}")
    if answers.anthropic_api_key:
        lines.append(f"ANTHROPIC_API_KEY={answers.anthropic_api_key}")
    if answers.github_token:
        lines.append(f"GITHUB_TOKEN={answers.github_token}")
    env_path = root / ".env"
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_path


def run_init(root: Path | None = None) -> None:
    """Interactive prompts -> factory.yaml + .env."""
    root = root or Path.cwd()
    click.echo("Software Factory bootstrap - answers are written to factory.yaml/.env\n")

    provider = click.prompt("Default agent provider", type=click.Choice(["cursor", "claude"]),
                            default="cursor")
    default_model = click.prompt(
        "Default model",
        default="composer-2.5" if provider == "cursor" else "claude-sonnet-latest",
    )
    answers = InitAnswers(
        provider=provider,
        topology=click.prompt("Topology", type=click.Choice(["in_repo", "control_plane"]),
                              default="in_repo"),
        default_model=default_model,
        max_cost_per_day=click.prompt("Max estimated cost per day (USD)", type=float,
                                      default=25.0),
        max_concurrent_agents=click.prompt("Max concurrent coding agents", type=int,
                                           default=3),
        sandbox_mode=click.prompt("Sandbox mode",
                                  type=click.Choice(["auto", "docker", "subprocess"]),
                                  default="auto"),
        scheduler_runner=click.prompt("Where do routines run",
                                      type=click.Choice(["local", "ci"]), default="local"),
        deploy_environment=click.prompt("Protected GitHub Environment name",
                                        default="production"),
        cursor_api_key=click.prompt("CURSOR_API_KEY (empty to skip)", default="",
                                    hide_input=True, show_default=False),
        anthropic_api_key=click.prompt("ANTHROPIC_API_KEY (empty to skip)", default="",
                                       hide_input=True, show_default=False),
        github_token=click.prompt("GITHUB_TOKEN (empty to skip)", default="",
                                  hide_input=True, show_default=False),
    )
    config_path = write_config(answers, root)
    env_path = write_env(answers, root)
    click.echo(f"\nwrote {config_path}")
    click.echo(f"wrote {env_path} (gitignored)")
    click.echo(
        "\nNext steps:\n"
        "  1. factory run \"your first task\"\n"
        "  2. factory dashboard --with-scheduler\n"
        "  3. Protect the GitHub Environment with required reviewers for deploys."
    )
