"""Orchestrator + integrator pipeline tests with a scripted backend."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from factory.backends import RunResult, RunStatus, Usage
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig, Provider
from factory.integrator import Integrator
from factory.orchestrator import BudgetExceeded, Orchestrator, Plan
from factory.state import StateStore, TaskStatus, UnitStatus
from tests.conftest import run_git


class ScriptedBackend:
    """Simulates agents: plans work units, writes owned files, resolves conflicts."""

    name = "scripted"

    def __init__(self, plan: dict, conflict_file: str | None = None) -> None:
        self._plan = plan
        self._conflict_file = conflict_file

    def run(
        self,
        *,
        prompt: str,
        cwd: str,
        model: str,
        timeout_seconds: int = 900,
        mcp_servers: dict[str, Any] | None = None,
    ) -> RunResult:
        if "# Role: Orchestrator" in prompt:
            output = json.dumps(self._plan)
        elif "# Role: Coder" in prompt:
            self._act_as_coder(prompt, Path(cwd))
            output = "implemented"
        elif "# Role: Conflict Resolver" in prompt:
            self._resolve_conflicts(Path(cwd))
            output = "resolved"
        else:
            output = "ok"
        return RunResult(
            status=RunStatus.FINISHED,
            output=output,
            usage=Usage(prompt_tokens=100, completion_tokens=50),
            provider=self.name,
            model=model,
        )

    def _act_as_coder(self, prompt: str, worktree: Path) -> None:
        owned_line = prompt.split("You own ONLY these files: ")[1].splitlines()[0]
        for rel in (p.strip() for p in owned_line.split(",")):
            target = worktree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# generated for {rel}\n", encoding="utf-8")
        if self._conflict_file:
            (worktree / self._conflict_file).write_text(
                f"conflicting content from {worktree.name}\n", encoding="utf-8"
            )
        run_git(["add", "-A"], worktree)
        run_git(["commit", "-m", "feat: implement unit"], worktree)

    def _resolve_conflicts(self, cwd: Path) -> None:
        conflicted = run_git(["diff", "--name-only", "--diff-filter=U"], cwd).splitlines()
        for rel in conflicted:
            (cwd / rel).write_text("resolved content\n", encoding="utf-8")
            run_git(["add", rel], cwd)
        subprocess.run(
            ["git", "commit", "--no-edit"], cwd=str(cwd), capture_output=True, timeout=60
        )


TWO_UNIT_PLAN = {
    "contract": "module a exposes fn_a(); module b exposes fn_b()",
    "work_units": [
        {"id": "unit-1", "title": "module a", "description": "implement a",
         "owned_files": ["src/a.py", "tests/test_a.py"]},
        {"id": "unit-2", "title": "module b", "description": "implement b",
         "owned_files": ["src/b.py", "tests/test_b.py"]},
    ],
}


def build_orchestrator(repo: Path, backend) -> tuple[Orchestrator, StateStore]:
    cfg = FactoryConfig()
    store = StateStore(repo / ".factory" / "factory.db")
    registry = BackendRegistry(cfg)
    registry.register(Provider.CURSOR, backend)
    registry.register(Provider.CLAUDE, backend)
    integrator = Integrator(repo, cfg, store, registry)
    return Orchestrator(repo, cfg, store, registry, integrator), store


def test_full_task_two_parallel_units(git_repo):
    orch, store = build_orchestrator(git_repo, ScriptedBackend(TWO_UNIT_PLAN))
    task = store.create_task("build modules", "a and b")

    branch = orch.run_task(task)

    assert branch == f"factory/{task.id}/integration"
    files = run_git(["ls-tree", "-r", "--name-only", branch], git_repo).splitlines()
    assert {"src/a.py", "src/b.py", "tests/test_a.py", "tests/test_b.py"} <= set(files)
    loaded = store.get_task(task.id)
    assert loaded.branch == branch
    assert all(u.status is UnitStatus.INTEGRATED for u in store.units_for_task(task.id))
    # Worktrees cleaned up after integration.
    assert not any((git_repo / ".factory" / "worktrees").glob("unit-*"))


def test_conflicting_units_resolved_by_agent(git_repo):
    backend = ScriptedBackend(TWO_UNIT_PLAN, conflict_file="shared.txt")
    orch, store = build_orchestrator(git_repo, backend)
    task = store.create_task("conflict task", "both touch shared.txt")

    branch = orch.run_task(task)

    content = run_git(["show", f"{branch}:shared.txt"], git_repo)
    assert content == "resolved content"


def test_plan_rejects_overlapping_ownership():
    bad = {
        "contract": "c",
        "work_units": [
            {"id": "u1", "title": "t", "description": "d", "owned_files": ["same.py"]},
            {"id": "u2", "title": "t", "description": "d", "owned_files": ["same.py"]},
        ],
    }
    with pytest.raises(ValueError, match="owned by both"):
        Plan.model_validate(bad)


def test_budget_exceeded_blocks_task(git_repo):
    orch, store = build_orchestrator(git_repo, ScriptedBackend(TWO_UNIT_PLAN))
    orch.config.budgets.max_tokens_per_task = 10  # below one run's usage
    task = store.create_task("expensive", "d")

    with pytest.raises(BudgetExceeded):
        orch.run_task(task)
    assert store.get_task(task.id).status is TaskStatus.BLOCKED


def test_startup_reconcile_prunes_orphans_and_requeues(git_repo):
    orch, store = build_orchestrator(git_repo, ScriptedBackend(TWO_UNIT_PLAN))
    task = store.create_task("interrupted", "d")
    store.update_task(task.id, status=TaskStatus.CODING)
    unit = store.create_unit(task.id, "u", "d", ["f.py"])
    store.update_unit(unit.id, status=UnitStatus.RUNNING)
    # Simulate a crash leaving an orphaned worktree dir on disk.
    orphan = git_repo / ".factory" / "worktrees" / "unit-dead"
    orphan.mkdir(parents=True)
    (orphan / "leftover.txt").write_text("x", encoding="utf-8")

    resumable = orch.startup_reconcile()

    assert [t.id for t in resumable] == [task.id]
    assert store.units_for_task(task.id)[0].status is UnitStatus.PENDING
    assert not orphan.exists()
