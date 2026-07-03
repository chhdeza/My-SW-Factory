"""Task orchestration: contract phase, decomposition, parallel coding, integration.

The orchestrator agent first produces a shared interface contract and a set of
work units with disjoint file ownership. Coders then run in parallel (bounded
by ``budgets.max_concurrent_agents``), each in its own sandboxed git worktree,
and the integrator serializes the merges.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from factory.agents import compose_prompt
from factory.backends.base import RunResult
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig
from factory.integrator import IntegrationError, Integrator
from factory.parsing import extract_json
from factory.state import StateStore, Task, TaskStatus, UnitStatus, WorkUnit

logger = logging.getLogger(__name__)


class PlannedUnit(BaseModel):
    id: str
    title: str
    description: str
    owned_files: list[str] = Field(min_length=1)


class Plan(BaseModel):
    contract: str
    work_units: list[PlannedUnit] = Field(min_length=1, max_length=8)

    @field_validator("work_units")
    @classmethod
    def _ownership_disjoint(cls, units: list[PlannedUnit]) -> list[PlannedUnit]:
        seen: dict[str, str] = {}
        for unit in units:
            for path in unit.owned_files:
                if path in seen:
                    raise ValueError(
                        f"file {path!r} owned by both {seen[path]!r} and {unit.id!r}"
                    )
                seen[path] = unit.id
        return units


class OrchestrationError(Exception):
    pass


class BudgetExceeded(Exception):
    pass


@dataclass
class BudgetTracker:
    max_tokens: int
    max_cost_usd: float
    tokens: int = 0
    cost_usd: float = 0.0
    runs: list[RunResult] = field(default_factory=list)

    def add(self, result: RunResult) -> None:
        self.runs.append(result)
        self.tokens += result.usage.total_tokens
        self.cost_usd += result.usage.estimated_cost_usd

    def check(self) -> None:
        if self.tokens > self.max_tokens or self.cost_usd > self.max_cost_usd:
            raise BudgetExceeded(
                f"task budget exceeded: {self.tokens} tokens (max {self.max_tokens}), "
                f"${self.cost_usd:.2f} (max ${self.max_cost_usd:.2f})"
            )


class Orchestrator:
    def __init__(
        self,
        repo_root: str | Path,
        config: FactoryConfig,
        store: StateStore,
        registry: BackendRegistry,
        integrator: Integrator | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        self.store = store
        self.registry = registry
        self.integrator = integrator or Integrator(repo_root, config, store, registry)

    # -- crash recovery -------------------------------------------------------

    def startup_reconcile(self) -> list[Task]:
        """Requeue interrupted units, prune orphaned worktrees, return resumable tasks."""
        resumable = self.store.reconcile()
        self.integrator.reconcile_worktrees()
        return resumable

    # -- pipeline stages --------------------------------------------------------

    def plan(self, task: Task, budget: BudgetTracker) -> tuple[str, list[WorkUnit]]:
        """Contract phase: decompose the task, persist units with file ownership."""
        self.store.update_task(task.id, status=TaskStatus.PLANNING)
        prompt = compose_prompt("orchestrator", task=f"{task.title}\n\n{task.description}")
        result = self.registry.run("orchestrator", prompt, cwd=str(self.repo_root))
        budget.add(result)
        budget.check()
        if not result.ok:
            raise OrchestrationError(f"planning failed: {result.error or result.status}")
        try:
            plan = Plan.model_validate(extract_json(result.output))
        except ValueError as exc:
            raise OrchestrationError(f"invalid plan from orchestrator agent: {exc}") from exc

        self.store.update_task(task.id, contract=plan.contract)
        units = [
            self.store.create_unit(task.id, planned.title, planned.description,
                                   planned.owned_files)
            for planned in plan.work_units
        ]
        logger.info(
            "task planned",
            extra={"operation": "plan", "task": task.id, "units": len(units)},
        )
        return plan.contract, units

    def code(self, task: Task, units: list[WorkUnit], contract: str,
             budget: BudgetTracker) -> None:
        """Run coder agents in parallel, each in its own worktree."""
        self.store.update_task(task.id, status=TaskStatus.CODING)
        max_workers = self.config.budgets.max_concurrent_agents
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._code_unit, unit, contract): unit for unit in units
            }
            failures: list[str] = []
            for future in as_completed(futures):
                unit = futures[future]
                try:
                    result = future.result()
                    budget.add(result)
                except Exception as exc:  # noqa: BLE001 - collected and re-raised below
                    failures.append(f"{unit.id}: {exc}")
                    self.store.update_unit(unit.id, status=UnitStatus.FAILED, error=str(exc))
        budget.check()
        if failures:
            raise OrchestrationError("coding failed: " + "; ".join(failures))

    def _code_unit(self, unit: WorkUnit, contract: str) -> RunResult:
        self.store.update_unit(unit.id, status=UnitStatus.RUNNING)
        worktree = self.integrator.create_worktree(unit)
        prompt = compose_prompt(
            "coder",
            task=(
                f"{unit.title}\n\n{unit.description}\n\n"
                f"You own ONLY these files: {', '.join(unit.owned_files)}"
            ),
            context=f"Shared interface contract:\n{contract}",
        )
        result = self.registry.run("coder", prompt, cwd=str(worktree))
        if not result.ok:
            self.store.update_unit(unit.id, status=UnitStatus.FAILED, error=result.error)
            raise OrchestrationError(f"coder failed on {unit.id}: {result.error}")
        self.store.update_unit(unit.id, status=UnitStatus.CODED)
        return result

    def run_task(self, task: Task) -> str:
        """Plan -> code -> integrate. Returns the integration branch name.

        Gates, PR, and merge are applied by the pipeline on top of the branch.
        """
        budget = BudgetTracker(
            max_tokens=self.config.budgets.max_tokens_per_task,
            max_cost_usd=self.config.budgets.max_cost_usd_per_task,
        )
        try:
            contract, units = self.plan(task, budget)
            self.code(task, units, contract, budget)
            self.store.update_task(task.id, status=TaskStatus.INTEGRATING)
            branch = self.integrator.integrate(task, self.store.units_for_task(task.id),
                                               contract)
            self.store.update_task(task.id, branch=branch)
            for unit in self.store.units_for_task(task.id):
                self.integrator.remove_worktree(unit)
            return branch
        except BudgetExceeded as exc:
            self.store.update_task(task.id, status=TaskStatus.BLOCKED, error=str(exc))
            raise
        except (OrchestrationError, IntegrationError) as exc:
            self.store.update_task(task.id, status=TaskStatus.FAILED, error=str(exc))
            raise
