"""Gate tests: profiles, quality gate, security gate."""

import json
import sys
from typing import Any

from factory.backends import RunResult, RunStatus, Usage
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig, LanguageProfile, Provider, SandboxConfig
from factory.gates.profiles import detect_profiles
from factory.gates.quality import QualityGate
from factory.gates.security import SecurityGate, severity_at_least
from factory.sandbox import SandboxExecutor


class ReviewBackend:
    """Returns a canned LLM review verdict."""

    name = "review"

    def __init__(self, verdict: str = "pass", findings: list | None = None) -> None:
        self._verdict = verdict
        self._findings = findings or []

    def run(self, *, prompt: str, cwd: str, model: str, timeout_seconds: int = 900,
            mcp_servers: dict[str, Any] | None = None) -> RunResult:
        self.last_mcp_servers = mcp_servers
        return RunResult(
            status=RunStatus.FINISHED,
            output=json.dumps({"verdict": self._verdict, "findings": self._findings}),
            usage=Usage(),
            provider=self.name,
            model=model,
        )


def make_env(backend=None):
    cfg = FactoryConfig()
    # Pin lint/test to this interpreter so the sandbox uses the test venv's
    # tools regardless of what's on PATH.
    cfg.gates.profiles["python"] = LanguageProfile(
        detect=["pyproject.toml"],
        lint=[sys.executable, "-m", "ruff", "check", "."],
        test=[sys.executable, "-m", "pytest", "-q"],
    )
    cfg.sandbox = SandboxConfig(
        mode="subprocess",
        command_allowlist=["python", "ruff", "actionlint", "bandit", "semgrep",
                           "gitleaks", "pip-audit", "npm"],
        timeout_seconds=120,
    )
    sandbox = SandboxExecutor(cfg.sandbox)
    registry = BackendRegistry(cfg)
    backend = backend or ReviewBackend()
    registry.register(Provider.CURSOR, backend)
    registry.register(Provider.CLAUDE, backend)
    return cfg, sandbox, registry


def make_python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='0'\n", encoding="utf-8"
    )
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    return tmp_path


# -- profiles ------------------------------------------------------------------


def test_detect_python_profile(tmp_path):
    make_python_project(tmp_path)
    cfg = FactoryConfig()
    names = [p.name for p in detect_profiles(tmp_path, cfg.gates)]
    assert names == ["python"]


def test_detect_custom_profile(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    cfg = FactoryConfig()
    cfg.gates.custom["go"] = LanguageProfile(
        detect=["go.mod"], lint=["golangci-lint", "run"], test=["go", "test", "./..."]
    )
    profiles = detect_profiles(tmp_path, cfg.gates)
    assert profiles[0].name == "go"
    assert profiles[0].test == ["go", "test", "./..."]


# -- quality gate -----------------------------------------------------------------


def test_quality_gate_passes_clean_project(tmp_path):
    project = make_python_project(tmp_path)
    cfg, sandbox, registry = make_env()
    cfg.gates.quality.coverage.enabled = False  # coverage covered separately
    gate = QualityGate(cfg, sandbox, registry)

    report = gate.run(project, diff_text="+ def add(a, b): return a + b")

    assert report.passed, report.summary()
    names = {c.name for c in report.checks}
    assert {"lint", "tests", "actionlint", "llm_review"} <= names


def test_quality_gate_fails_on_broken_test(tmp_path):
    project = make_python_project(tmp_path)
    (project / "test_app.py").write_text(
        "def test_broken():\n    assert 1 == 2\n", encoding="utf-8"
    )
    cfg, sandbox, registry = make_env()
    cfg.gates.quality.coverage.enabled = False
    gate = QualityGate(cfg, sandbox, registry)

    report = gate.run(project)

    assert not report.passed
    assert any(c.name == "tests" for c in report.failures)


def test_quality_gate_fails_on_llm_review_fail(tmp_path):
    project = make_python_project(tmp_path)
    cfg, sandbox, registry = make_env(
        ReviewBackend("fail", [{"severity": "high", "file": "app.py", "issue": "bug"}])
    )
    cfg.gates.quality.coverage.enabled = False
    gate = QualityGate(cfg, sandbox, registry)

    report = gate.run(project, diff_text="+ bad code")

    failures = {c.name for c in report.failures}
    assert "llm_review" in failures


def test_actionlint_skipped_without_workflow_changes(tmp_path):
    project = make_python_project(tmp_path)
    cfg, sandbox, registry = make_env()
    cfg.gates.quality.coverage.enabled = False
    gate = QualityGate(cfg, sandbox, registry)

    report = gate.run(project, changed_files=["app.py"])

    actionlint = next(c for c in report.checks if c.name == "actionlint")
    assert actionlint.skipped


# -- security gate ------------------------------------------------------------


def test_security_gate_missing_tools_skip_not_fail(tmp_path):
    """Tools that aren't installed produce SKIP so the scaffold works out of the box."""
    project = make_python_project(tmp_path)
    cfg, sandbox, registry = make_env()
    gate = SecurityGate(cfg, sandbox, registry)

    report = gate.run(project, diff_text="+ code")

    assert report.passed, report.summary()
    llm = next(c for c in report.checks if c.name == "llm_review")
    assert llm.passed and not llm.skipped


def test_security_llm_review_fails_gate(tmp_path):
    project = make_python_project(tmp_path)
    backend = ReviewBackend(
        "fail", [{"severity": "critical", "file": "app.py", "issue": "sql injection"}]
    )
    cfg, sandbox, registry = make_env(backend)
    gate = SecurityGate(cfg, sandbox, registry)

    report = gate.run(project, diff_text="+ query = f'SELECT {x}'")

    assert not report.passed
    assert "sql injection" in report.failures[0].details


def test_enabled_mcp_servers_passed_to_security_reviewer(tmp_path, monkeypatch):
    monkeypatch.setenv("SONAR_TOKEN", "tok")
    project = make_python_project(tmp_path)
    backend = ReviewBackend()
    cfg, sandbox, registry = make_env(backend)
    cfg.mcps = {
        "sonarqube": type(cfg).model_validate(
            {"mcps": {"s": {"enabled": True, "command": ["npx", "sonar-mcp"],
                            "env": {"SONAR_TOKEN": "${SONAR_TOKEN}"}}}}
        ).mcps["s"]
    }
    gate = SecurityGate(cfg, sandbox, registry)

    gate.run(project, diff_text="+ x")

    assert backend.last_mcp_servers is not None
    assert backend.last_mcp_servers["sonarqube"]["env"]["SONAR_TOKEN"] == "tok"


def test_severity_ordering():
    assert severity_at_least("critical", "high")
    assert severity_at_least("high", "high")
    assert not severity_at_least("medium", "high")
    assert not severity_at_least("unknown", "high")
