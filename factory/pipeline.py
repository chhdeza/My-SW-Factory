"""End-to-end task pipeline: intake -> plan -> code -> integrate -> gates ->
review -> risk-based merge.

Works local-first: when no GitHub token/remote is configured, the PR step is
skipped and low-risk changes merge into the default branch locally; high-risk
changes are left on their integration branch for a human.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from factory.agents import compose_prompt
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig, load_config
from factory.gates import GateReport
from factory.gates.merge_policy import assess_risk, merge_decision
from factory.gates.quality import QualityGate
from factory.gates.security import SecurityGate
from factory.github_api import GitHubClient, GitHubError, detect_repo
from factory.intake import task_from_issue, task_from_text
from factory.integrator import GitError, Integrator, git
from factory.orchestrator import BudgetExceeded, OrchestrationError, Orchestrator
from factory.parsing import extract_json
from factory.sandbox import SandboxExecutor
from factory.state import StateStore, Task, TaskStatus

logger = logging.getLogger(__name__)

# A healer is wired in by factory.selfheal; signature: (task, branch, reports) -> bool
Healer = Callable[[Task, str, list[GateReport]], bool]


@dataclass
class Outcome:
    ok: bool
    summary: str
    branch: str = ""
    pr_number: int | None = None
    decision: str = ""


class Pipeline:
    def __init__(
        self,
        repo_root: Path,
        config: FactoryConfig,
        store: StateStore,
        registry: BackendRegistry,
        healer: Healer | None = None,
        on_agent_run: Callable | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.config = config
        self.store = store
        self.registry = registry
        from factory.metrics.observe import attach_observability

        self.metrics, self.tracer = attach_observability(registry, repo_root, config)
        self.integrator = Integrator(repo_root, config, store, registry)
        self.integrator.metrics = self.metrics
        self.orchestrator = Orchestrator(repo_root, config, store, registry, self.integrator)
        self.sandbox = SandboxExecutor(config.sandbox)
        self.quality_gate = QualityGate(config, self.sandbox, registry)
        self.security_gate = SecurityGate(config, self.sandbox, registry)
        self.healer = healer
        self._github: GitHubClient | None = None

    @classmethod
    def from_repo(cls, repo_root: Path) -> Pipeline:
        config = load_config(repo_root)
        store = StateStore(repo_root / ".factory" / "factory.db")
        registry = BackendRegistry(config)
        pipeline = cls(repo_root, config, store, registry)
        try:
            from factory.selfheal.loop import GateHealer

            pipeline.healer = GateHealer(pipeline).heal
        except ImportError:  # selfheal optional at construction time
            pipeline.healer = None
        return pipeline

    # -- intake ------------------------------------------------------------

    def intake_text(self, text: str) -> Task:
        task = task_from_text(self.store, text, source="cli")
        self.metrics.record_event("task_created", task.id, task.title)
        return task

    def intake_issue(self, issue_number: int) -> Task:
        task = task_from_issue(self.store, self.github(), issue_number)
        self.metrics.record_event("task_created", task.id, task.title)
        return task

    def github(self) -> GitHubClient:
        if self._github is None:
            repo = self.config.github.repo or detect_repo(str(self.repo_root))
            self._github = GitHubClient(repo)
        return self._github

    def _github_available(self) -> bool:
        try:
            self.github()
            return True
        except GitHubError:
            return False

    # -- pipeline ---------------------------------------------------------------

    def run(self, task: Task) -> Outcome:
        """Run the full pipeline. Orchestration errors surface in the outcome."""
        self.orchestrator.startup_reconcile()
        try:
            branch = self.orchestrator.run_task(task)
        except (OrchestrationError, BudgetExceeded, GitError) as exc:
            return Outcome(ok=False, summary=f"orchestration failed: {exc}")

        return self.gate_and_merge(task, branch)

    def gate_and_merge(self, task: Task, branch: str) -> Outcome:
        """Gates -> (self-heal) -> reviewer -> PR -> risk-based merge."""
        self.store.update_task(task.id, status=TaskStatus.GATING)
        base = self.config.github.default_branch
        commit_count = git(["rev-list", "--count", f"{base}..{branch}"], cwd=self.repo_root)
        for _ in range(int(commit_count or 0)):
            self.metrics.record_event("commit", task.id)
        reports, gate_worktree = self._run_gates(task, branch)

        if not all(r.passed for r in reports):
            healed = False
            if self.healer is not None and self.config.selfheal.enabled:
                self.store.update_task(task.id, status=TaskStatus.HEALING)
                healed = self.healer(task, branch, reports)
            if healed:
                reports, gate_worktree = self._run_gates(task, branch)
            if not all(r.passed for r in reports):
                self.store.update_task(
                    task.id, status=TaskStatus.BLOCKED,
                    error="; ".join(r.summary() for r in reports if not r.passed),
                )
                return Outcome(
                    ok=False, branch=branch, decision="needs_human",
                    summary="gates failed after self-heal: "
                    + "; ".join(r.summary() for r in reports if not r.passed),
                )

        changed_files, diff_lines, diff_text = self._diff_stats(branch)
        reviewer_risk, review_summary = self._reviewer(diff_text, gate_worktree)
        security_flagged = any(
            r.gate == "security" and (r.failures or any(
                not c.passed for c in r.checks if not c.skipped))
            for r in reports
        )
        assessment = assess_risk(
            changed_files, diff_lines, self.config.merge,
            security_flagged=security_flagged, reviewer_risk=reviewer_risk,
        )
        decision = merge_decision(assessment, self.config.merge)

        if self._github_available():
            return self._finish_remote(task, branch, decision, assessment.reasons,
                                       review_summary)
        return self._finish_local(task, branch, decision, assessment.reasons)

    # -- helpers ------------------------------------------------------------------

    def _run_gates(self, task: Task, branch: str) -> tuple[list[GateReport], Path]:
        """Run gates in a throwaway worktree checked out at the branch."""
        gate_path = self.repo_root / ".factory" / "worktrees" / f"gate-{task.id}"
        if gate_path.exists():
            self.integrator._remove_worktree_path(gate_path)
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        git(["worktree", "add", "--detach", str(gate_path), branch], cwd=self.repo_root)
        try:
            changed_files, _, diff_text = self._diff_stats(branch)
            quality = self.quality_gate.run(gate_path, diff_text, changed_files)
            security = self.security_gate.run(gate_path, diff_text)
            for report in (quality, security):
                self.metrics.record_event(
                    "gate_passed" if report.passed else "gate_failed",
                    task.id, report.summary(),
                )
            return [quality, security], gate_path
        finally:
            self.integrator._remove_worktree_path(gate_path)

    def _diff_stats(self, branch: str) -> tuple[list[str], int, str]:
        base = self.config.github.default_branch
        changed = git(["diff", "--name-only", f"{base}...{branch}"],
                      cwd=self.repo_root).splitlines()
        numstat = git(["diff", "--numstat", f"{base}...{branch}"],
                      cwd=self.repo_root).splitlines()
        total = 0
        for line in numstat:
            parts = line.split("\t")
            if len(parts) >= 2:
                for value in parts[:2]:
                    total += int(value) if value.isdigit() else 0
        diff_text = git(["diff", f"{base}...{branch}"], cwd=self.repo_root)
        return changed, total, diff_text

    def _reviewer(self, diff_text: str, cwd: Path) -> tuple[str, str]:
        prompt = compose_prompt(
            "reviewer",
            task="Review this diff:\n\n```diff\n" + diff_text[:60_000] + "\n```",
            context=f"High-risk diff size threshold: "
                    f"{self.config.merge.high_risk.max_diff_lines} lines",
        )
        result = self.registry.run("reviewer", prompt, cwd=str(self.repo_root))
        if not result.ok:
            logger.warning("reviewer agent failed; treating as high risk",
                           extra={"operation": "review", "error": result.error})
            return "high", "reviewer agent unavailable"
        try:
            verdict = extract_json(result.output)
        except ValueError:
            return "high", "unparseable reviewer output"
        risk = str(verdict.get("risk", "high"))
        if verdict.get("verdict") == "request_changes":
            risk = "high"
        return risk, str(verdict.get("summary", ""))

    def _finish_remote(
        self, task: Task, branch: str, decision: str, reasons: list[str], review: str
    ) -> Outcome:
        github = self.github()
        base = self.config.github.default_branch
        git(["push", "-u", "origin", branch], cwd=self.repo_root, timeout=300)
        body = review or task.description
        if reasons:
            body += "\n\nRisk assessment:\n" + "\n".join(f"- {r}" for r in reasons)
        pr = github.create_pr(title=f"factory: {task.title}", head=branch, base=base,
                              body=body)
        number = int(pr["number"])
        self.store.update_task(task.id, status=TaskStatus.PR_OPEN)
        self.metrics.record_event("pr_opened", task.id, f"#{number}")

        if decision == "needs_human":
            github.add_labels(number, [self.config.merge.needs_human_label])
            return Outcome(ok=True, branch=branch, pr_number=number, decision=decision,
                           summary=f"PR #{number} opened, held for human review: "
                                   + "; ".join(reasons))
        if github.pr_checks_passed(number):
            github.merge_pr(number)
            self.store.update_task(task.id, status=TaskStatus.MERGED)
            self.metrics.record_event("pr_merged", task.id, f"#{number}")
            return Outcome(ok=True, branch=branch, pr_number=number, decision=decision,
                           summary=f"PR #{number} auto-merged (low risk, gates + CI green)")
        return Outcome(ok=True, branch=branch, pr_number=number, decision="needs_human",
                       summary=f"PR #{number} opened; CI not green yet, left for CI/self-heal")

    def _finish_local(
        self, task: Task, branch: str, decision: str, reasons: list[str]
    ) -> Outcome:
        base = self.config.github.default_branch
        if decision == "auto_merge":
            current = git(["branch", "--show-current"], cwd=self.repo_root)
            if current == base:
                git(["merge", "--no-ff", "--no-edit", branch], cwd=self.repo_root)
            else:
                # Base isn't checked out here - merge via a temp worktree.
                merge_path = self.repo_root / ".factory" / "worktrees" / f"merge-{task.id}"
                if merge_path.exists():
                    self.integrator._remove_worktree_path(merge_path)
                merge_path.parent.mkdir(parents=True, exist_ok=True)
                git(["worktree", "add", str(merge_path), base], cwd=self.repo_root)
                try:
                    git(["merge", "--no-ff", "--no-edit", branch], cwd=merge_path)
                finally:
                    self.integrator._remove_worktree_path(merge_path)
            self.store.update_task(task.id, status=TaskStatus.MERGED)
            self.metrics.record_event("pr_merged", task.id, f"local merge of {branch}")
            return Outcome(ok=True, branch=branch, decision=decision,
                           summary=f"merged {branch} into {base} locally (low risk)")
        self.store.update_task(task.id, status=TaskStatus.PR_OPEN)
        return Outcome(ok=True, branch=branch, decision=decision,
                       summary=f"branch {branch} held for human review: "
                               + "; ".join(reasons))
