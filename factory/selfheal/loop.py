"""Gate-failure healing: classify -> fixer agent -> re-gate.

The pipeline calls ``GateHealer.heal`` when gates fail on an integration
branch. The fixer agent works in a worktree checked out at that branch and
commits the minimal fix; the pipeline then re-runs the gates. All attempts go
through the guardrail ledger.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from factory.agents import compose_prompt
from factory.gates import GateReport
from factory.integrator import GitError, git
from factory.selfheal.guardrails import HealLedger, make_signature
from factory.state import Task

if TYPE_CHECKING:
    from factory.pipeline import Pipeline

logger = logging.getLogger(__name__)

MAX_FAILURE_DETAIL_CHARS = 8_000


def failure_signature(reports: list[GateReport]) -> str:
    parts = []
    for report in reports:
        for check in report.failures:
            parts.append(f"{report.gate}:{check.name}:{check.details[:200]}")
    return make_signature(*parts)


def failure_summary(reports: list[GateReport]) -> str:
    lines = []
    for report in reports:
        for check in report.failures:
            details = check.details[:MAX_FAILURE_DETAIL_CHARS]
            lines.append(f"[{report.gate}/{check.name}]\n{details}")
    return "\n\n".join(lines)


class GateHealer:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline
        self.ledger = HealLedger(
            pipeline.repo_root / ".factory" / "factory.db",
            backoff_base_seconds=pipeline.config.selfheal.backoff_base_seconds,
        )

    def heal(self, task: Task, branch: str, reports: list[GateReport]) -> bool:
        """Dispatch the fixer agent onto the failing branch. True when a fix landed."""
        config = self.pipeline.config.selfheal
        signature = failure_signature(reports)
        if not self.ledger.allowed(signature, config.max_fix_attempts):
            logger.warning(
                "healing suppressed by guardrails",
                extra={"operation": "selfheal", "signature": signature, "task": task.id},
            )
            return False
        attempt = self.ledger.record(signature)
        self.pipeline.metrics.record_event("heal_started", task.id, signature)

        repo_root = self.pipeline.repo_root
        fix_path = repo_root / ".factory" / "worktrees" / f"heal-{task.id}-{attempt}"
        if fix_path.exists():
            self.pipeline.integrator._remove_worktree_path(fix_path)
        fix_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            git(["worktree", "add", str(fix_path), branch], cwd=repo_root)
        except GitError as exc:
            logger.error("could not create heal worktree",
                         extra={"operation": "selfheal", "error": str(exc)})
            return False

        try:
            before = git(["rev-parse", "HEAD"], cwd=fix_path)
            prompt = compose_prompt(
                "fixer",
                task=(
                    "The following gate checks failed on this branch. Fix the root cause "
                    "and commit with a conventional fix: message.\n\n"
                    + failure_summary(reports)
                ),
                context=f"Failure signature: {signature} (attempt {attempt})",
            )
            result = self.pipeline.registry.run("fixer", prompt, cwd=str(fix_path))
            if not result.ok:
                logger.warning("fixer agent failed",
                               extra={"operation": "selfheal", "error": result.error})
                return False
            after = git(["rev-parse", "HEAD"], cwd=fix_path)
            healed = after != before
            if healed:
                self.pipeline.metrics.record_event("fix_applied", task.id, signature)
            if not healed:
                logger.warning("fixer agent produced no commit",
                               extra={"operation": "selfheal", "signature": signature})
            return healed
        finally:
            self.pipeline.integrator._remove_worktree_path(fix_path)
