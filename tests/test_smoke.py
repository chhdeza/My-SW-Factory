"""End-to-end smoke scenario (V1 Contract acceptance criteria).

Runs the whole vertical slice on a sample repo with scripted agents:
intake -> contract -> 2 parallel sandboxed coders -> gates (real ruff/pytest)
-> risk-based local merge -> metrics/dashboard, plus the self-heal path
(broken test -> fixer -> re-gate -> merge), crash-resume, and the
approval-gated deploy.
"""

import json
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from factory.backends import RunResult, RunStatus, Usage
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig, LanguageProfile, Provider, SandboxConfig
from factory.dashboard.app import create_app
from factory.deploy.gate import DeployGate
from factory.deploy.runner import DeployRunner
from factory.pipeline import Pipeline
from factory.selfheal.loop import GateHealer
from factory.state import StateStore, TaskStatus
from tests.conftest import run_git

PLAN = {
    "contract": "app.py exposes add(a, b); util.py exposes double(x)",
    "work_units": [
        {"id": "unit-app", "title": "add()", "description": "implement add",
         "owned_files": ["app.py", "test_app.py"]},
        {"id": "unit-util", "title": "double()", "description": "implement double",
         "owned_files": ["util.py", "test_util.py"]},
    ],
}

GOOD_FILES = {
    "app.py": "def add(a, b):\n    return a + b\n",
    "test_app.py": "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    "util.py": "def double(x):\n    return 2 * x\n",
    "test_util.py": (
        "from util import double\n\n\ndef test_double():\n    assert double(3) == 6\n"
    ),
}

BROKEN_TEST = "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n"


class SmokeBackend:
    """Scripted agents for the full pipeline. Optionally codes a broken test."""

    name = "smoke"

    def __init__(self, break_test: bool = False) -> None:
        self.break_test = break_test
        self.roles_seen: list[str] = []

    def run(self, *, prompt: str, cwd: str, model: str, timeout_seconds: int = 900,
            mcp_servers: dict[str, Any] | None = None) -> RunResult:
        role = _role_of(prompt)
        self.roles_seen.append(role)
        output = "ok"
        if role == "orchestrator":
            output = json.dumps(PLAN)
        elif role == "coder":
            self._code(prompt, Path(cwd))
            output = "implemented"
        elif role == "fixer":
            self._fix(Path(cwd))
            output = "fixed"
        elif role in ("quality", "security"):
            output = json.dumps({"verdict": "pass", "findings": []})
        elif role == "reviewer":
            output = json.dumps({"verdict": "approve", "risk": "low",
                                 "reasons": [], "summary": "clean change"})
        return RunResult(status=RunStatus.FINISHED, output=output,
                         usage=Usage(prompt_tokens=200, completion_tokens=100),
                         provider=self.name, model=model)

    def _code(self, prompt: str, worktree: Path) -> None:
        owned = prompt.split("You own ONLY these files: ")[1].splitlines()[0]
        for rel in (p.strip() for p in owned.split(",")):
            content = GOOD_FILES[rel]
            if self.break_test and rel == "test_app.py":
                content = BROKEN_TEST
            (worktree / rel).write_text(content, encoding="utf-8")
        run_git(["add", "-A"], worktree)
        run_git(["commit", "-m", "feat: implement unit"], worktree)

    def _fix(self, worktree: Path) -> None:
        target = worktree / "test_app.py"
        if target.exists():
            target.write_text(GOOD_FILES["test_app.py"], encoding="utf-8")
            run_git(["add", "-A"], worktree)
            run_git(["commit", "-m", "fix: correct broken assertion"], worktree)


def _role_of(prompt: str) -> str:
    for role in ("Orchestrator", "Coder", "Conflict Resolver", "Quality Reviewer",
                 "Security Engineer", "Final Reviewer", "Fixer", "Log Analyzer",
                 "CI Analyzer"):
        if f"# Role: {role}" in prompt:
            return {
                "Orchestrator": "orchestrator", "Coder": "coder",
                "Conflict Resolver": "conflict_resolver",
                "Quality Reviewer": "quality", "Security Engineer": "security",
                "Final Reviewer": "reviewer", "Fixer": "fixer",
                "Log Analyzer": "log_analyzer", "CI Analyzer": "ci_analyzer",
            }[role]
    return "unknown"


def smoke_config() -> FactoryConfig:
    cfg = FactoryConfig()
    cfg.gates.profiles["python"] = LanguageProfile(
        detect=["app.py"],
        lint=[sys.executable, "-m", "ruff", "check", "."],
        test=[sys.executable, "-m", "pytest", "-q"],
    )
    cfg.gates.quality.coverage.enabled = False   # sample app has no pyproject
    cfg.gates.security.bandit.enabled = False    # not installed in test env
    cfg.gates.security.semgrep.enabled = False
    cfg.gates.security.gitleaks.enabled = False
    cfg.gates.security.dep_audit.enabled = False
    cfg.sandbox = SandboxConfig(mode="subprocess",
                                command_allowlist=["python"], timeout_seconds=300)
    cfg.selfheal.backoff_base_seconds = 0
    return cfg


def make_pipeline(git_repo, backend) -> Pipeline:
    cfg = smoke_config()
    store = StateStore(git_repo / ".factory" / "factory.db")
    registry = BackendRegistry(cfg)
    registry.register(Provider.CURSOR, backend)
    registry.register(Provider.CLAUDE, backend)
    pipeline = Pipeline(git_repo, cfg, store, registry)
    pipeline.healer = GateHealer(pipeline).heal
    return pipeline


def test_smoke_happy_path_parallel_agents_to_local_merge(git_repo):
    backend = SmokeBackend()
    pipeline = make_pipeline(git_repo, backend)
    task = pipeline.intake_text("add an add() and double() function with tests")

    outcome = pipeline.run(task)

    assert outcome.ok, outcome.summary
    assert outcome.decision == "auto_merge"
    # >=2 parallel coders ran, plus orchestrator/quality/security/reviewer.
    assert backend.roles_seen.count("coder") == 2
    assert {"orchestrator", "quality", "security", "reviewer"} <= set(backend.roles_seen)
    # Merged into main with the sample app present.
    files = run_git(["ls-tree", "-r", "--name-only", "main"], git_repo)
    assert "app.py" in files and "util.py" in files
    assert pipeline.store.get_task(task.id).status is TaskStatus.MERGED
    # Metrics recorded runs and a merge event; a trace exists per agent run.
    from datetime import UTC, datetime

    agg = pipeline.metrics.day_aggregates(datetime.now(UTC).date().isoformat())
    assert agg["agent_runs"] >= 5
    assert agg["prs_merged"] == 1
    assert agg["commits"] >= 2
    assert len(pipeline.tracer.list_traces()) >= 5


def test_smoke_selfheal_fixes_broken_test_then_merges(git_repo):
    backend = SmokeBackend(break_test=True)
    pipeline = make_pipeline(git_repo, backend)
    task = pipeline.intake_text("add functions (one coder ships a broken test)")

    outcome = pipeline.run(task)

    assert outcome.ok, outcome.summary
    assert "fixer" in backend.roles_seen          # self-heal actually ran
    files = run_git(["ls-tree", "-r", "--name-only", "main"], git_repo)
    assert "app.py" in files
    content = run_git(["show", "main:test_app.py"], git_repo)
    assert "== 3" in content                      # the fix landed
    from datetime import UTC, datetime

    agg = pipeline.metrics.day_aggregates(datetime.now(UTC).date().isoformat())
    assert agg["fixes"] == 1


def test_smoke_high_risk_change_held_for_human(git_repo):
    backend = SmokeBackend()
    pipeline = make_pipeline(git_repo, backend)
    pipeline.config.merge.high_risk.max_diff_lines = 1  # force high risk
    task = pipeline.intake_text("any change is high risk now")

    outcome = pipeline.run(task)

    assert outcome.ok
    assert outcome.decision == "needs_human"
    assert pipeline.store.get_task(task.id).status is TaskStatus.PR_OPEN
    files = run_git(["ls-tree", "-r", "--name-only", "main"], git_repo)
    assert "app.py" not in files                  # NOT merged


def test_smoke_crash_resume_reconciles_on_next_run(git_repo):
    backend = SmokeBackend()
    pipeline = make_pipeline(git_repo, backend)
    # Simulate a crashed previous run: orphan worktree + RUNNING unit.
    dead = pipeline.store.create_task("crashed", "d")
    pipeline.store.update_task(dead.id, status=TaskStatus.CODING)
    unit = pipeline.store.create_unit(dead.id, "u", "d", ["x.py"])
    from factory.state import UnitStatus

    pipeline.store.update_unit(unit.id, status=UnitStatus.RUNNING)
    orphan = git_repo / ".factory" / "worktrees" / "unit-orphan"
    orphan.mkdir(parents=True)

    task = pipeline.intake_text("fresh task after crash")
    outcome = pipeline.run(task)

    assert outcome.ok
    assert not orphan.exists()                    # orphan pruned on startup
    from factory.state import UnitStatus

    assert pipeline.store.units_for_task(dead.id)[0].status is UnitStatus.PENDING


def test_smoke_deploy_stays_blocked_until_approved(git_repo):
    cfg = smoke_config()
    gate = DeployGate(git_repo, cfg)
    runner = DeployRunner(git_repo, cfg)
    gate.request("main")

    import pytest

    from factory.deploy.runner import DeployError

    with pytest.raises(DeployError):
        runner.execute()                          # blocked: not approved

    gate.approve(approver="human")
    assert "noop deploy of main completed" in runner.execute()


def test_smoke_dashboard_shows_activity(git_repo):
    backend = SmokeBackend()
    pipeline = make_pipeline(git_repo, backend)
    task = pipeline.intake_text("dashboard visibility task")
    pipeline.run(task)

    app = create_app(repo_root=git_repo, config=pipeline.config)
    http = TestClient(app)
    page = http.get("/").text
    assert "coder" in page                        # agent runs table
    assert task.id in page                        # tasks table
    runs = http.get("/api/runs").json()
    assert any(r["role"] == "orchestrator" for r in runs)
