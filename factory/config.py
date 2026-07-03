"""Configuration loading and validation.

`factory.yaml` is the single source of configuration; secrets come from the
environment (.env locally, Actions secrets in CI). Everything is validated with
Pydantic so invalid config fails fast at startup.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

FACTORY_DIR = ".factory"
CONFIG_FILENAME = "factory.yaml"

_ENV_REF = re.compile(r"\$\{(\w+)\}")


class Provider(StrEnum):
    CURSOR = "cursor"
    CLAUDE = "claude"


class Topology(StrEnum):
    IN_REPO = "in_repo"
    CONTROL_PLANE = "control_plane"


class ModelSpec(BaseModel):
    provider: Provider = Provider.CURSOR
    model: str = "composer-2.5"


class Budgets(BaseModel):
    max_tokens_per_task: int = 2_000_000
    max_cost_usd_per_task: float = 5.0
    max_cost_usd_per_day: float = 25.0
    max_concurrent_agents: int = Field(default=3, ge=1, le=16)


class SandboxConfig(BaseModel):
    mode: Literal["auto", "docker", "subprocess"] = "auto"
    docker_image: str = "python:3.12-slim"
    network: Literal["none", "bridge"] = "none"
    cpu_limit: float = 2.0
    memory_limit_mb: int = 2048
    timeout_seconds: int = 900
    command_allowlist: list[str] = Field(
        default_factory=lambda: ["python", "pytest", "ruff", "pip", "git"]
    )


class ToolGate(BaseModel):
    enabled: bool = True
    max_errors: int = 0


class CoverageGate(BaseModel):
    enabled: bool = True
    min_percent: float = 80.0
    allow_no_regression: bool = True


class SeverityGate(BaseModel):
    enabled: bool = True
    fail_level: str = "high"


class SemgrepGate(BaseModel):
    enabled: bool = True
    fail_severity: str = "error"
    # Rules source: "auto" = semgrep registry, or the opengrep-rules checkout
    # when the opengrep engine is used. Override with a path/URL to pin rules.
    rules: str = "auto"


class GitleaksGate(BaseModel):
    enabled: bool = True
    max_findings: int = 0


class DepAuditGate(BaseModel):
    enabled: bool = True
    fail_severity: str = "high"


class LLMReviewGate(BaseModel):
    enabled: bool = True


class QualityGates(BaseModel):
    enabled: bool = True
    ruff: ToolGate = ToolGate()
    pytest: LLMReviewGate = LLMReviewGate()
    coverage: CoverageGate = CoverageGate()
    actionlint: ToolGate = ToolGate()
    llm_review: LLMReviewGate = LLMReviewGate()


class SecurityGates(BaseModel):
    enabled: bool = True
    bandit: SeverityGate = SeverityGate()
    semgrep: SemgrepGate = SemgrepGate()
    gitleaks: GitleaksGate = GitleaksGate()
    dep_audit: DepAuditGate = DepAuditGate()
    llm_review: LLMReviewGate = LLMReviewGate()


class LanguageProfile(BaseModel):
    detect: list[str] = Field(default_factory=list)
    lint: list[str] | None = None
    test: list[str] | None = None


class GatesConfig(BaseModel):
    quality: QualityGates = QualityGates()
    security: SecurityGates = SecurityGates()
    profiles: dict[str, LanguageProfile] = Field(
        default_factory=lambda: {
            "python": LanguageProfile(detect=["pyproject.toml", "requirements.txt", "setup.py"]),
            "node": LanguageProfile(detect=["package.json"]),
        }
    )
    custom: dict[str, LanguageProfile] = Field(default_factory=dict)


class HighRiskRules(BaseModel):
    workflow_edits: bool = True
    dependency_upgrades: bool = True
    security_flagged: bool = True
    migrations: bool = True
    max_diff_lines: int = 500


class MergeConfig(BaseModel):
    auto_merge_low_risk: bool = True
    high_risk: HighRiskRules = HighRiskRules()
    needs_human_label: str = "needs-human-review"


class SelfHealConfig(BaseModel):
    enabled: bool = True
    ci_review: bool = True
    max_reruns: int = 2
    max_fix_attempts: int = 3
    backoff_base_seconds: int = 60
    workflow_edit_policy: Literal["strict", "normal"] = "strict"
    pr_token: Literal["github_token", "app", "pat"] = "github_token"


class SchedulerConfig(BaseModel):
    runner: Literal["local", "ci"] = "local"
    timezone: str = "UTC"
    command_allowlist: list[list[str]] = Field(default_factory=list)


class RoutineAction(BaseModel):
    type: Literal["task", "agent", "gate", "report", "maintenance", "command"]
    ref: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class Routine(BaseModel):
    enabled: bool = True
    cron: str
    timezone: str | None = None
    action: RoutineAction
    on_failure: Literal["log", "issue", "heal"] = "log"

    @field_validator("cron")
    @classmethod
    def _validate_cron(cls, v: str) -> str:
        if len(v.split()) != 5:
            raise ValueError(f"cron expression must have 5 fields, got: {v!r}")
        return v


class McpServer(BaseModel):
    enabled: bool = False
    transport: Literal["stdio", "http"] = "stdio"
    command: list[str] = Field(default_factory=list)
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)

    def resolved_env(self) -> dict[str, str]:
        """Resolve ${VAR} references from the process environment.

        Credentials are referenced by env var name in factory.yaml and never
        stored inline.
        """
        out: dict[str, str] = {}
        for key, value in self.env.items():
            match = _ENV_REF.fullmatch(value)
            out[key] = os.environ.get(match.group(1), "") if match else value
        return out


class TracingConfig(BaseModel):
    enabled: bool = True
    dir: str = f"{FACTORY_DIR}/traces"
    retention_days: int = 30
    redact: bool = True


class DeployConfig(BaseModel):
    environment: str = "production"
    hook: str = "noop"
    cloudrun: dict[str, str] = Field(default_factory=dict)
    compose_vm: dict[str, str] = Field(default_factory=dict)


class GitHubConfig(BaseModel):
    repo: str = ""
    default_branch: str = "main"


class FactoryConfig(BaseModel):
    provider: Provider = Provider.CURSOR
    topology: Topology = Topology.IN_REPO
    models: dict[str, ModelSpec] = Field(default_factory=lambda: {"default": ModelSpec()})
    budgets: Budgets = Budgets()
    sandbox: SandboxConfig = SandboxConfig()
    gates: GatesConfig = GatesConfig()
    merge: MergeConfig = MergeConfig()
    selfheal: SelfHealConfig = SelfHealConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    schedules: dict[str, Routine] = Field(default_factory=dict)
    mcps: dict[str, McpServer] = Field(default_factory=dict)
    tracing: TracingConfig = TracingConfig()
    deploy: DeployConfig = DeployConfig()
    github: GitHubConfig = GitHubConfig()

    def model_for_role(self, role: str) -> ModelSpec:
        return self.models.get(role) or self.models.get("default") or ModelSpec(
            provider=self.provider
        )

    @property
    def state_dir(self) -> Path:
        return Path(FACTORY_DIR)


def load_config(root: str | Path = ".") -> FactoryConfig:
    """Load and validate factory.yaml from the given repo root."""
    path = Path(root) / CONFIG_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"{CONFIG_FILENAME} not found in {Path(root).resolve()}. Run `factory init` first."
        )
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return FactoryConfig.model_validate(raw)
