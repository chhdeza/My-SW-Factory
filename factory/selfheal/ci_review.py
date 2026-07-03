"""GitHub Actions failure review and healing.

Triggered by the self-heal workflow (``workflow_run`` failure event) or
manually via ``factory heal``. For each failed run: pull the failed-job logs,
have the ci_analyzer agent classify the failure, then:

- transient  -> rerun failed jobs (up to ``selfheal.max_reruns`` per signature)
- fixable    -> fixer agent on a fix branch -> PR (workflow edits get the
                security gate + needs-human-review label)
- infra/etc. -> escalate as a GitHub issue
"""

from __future__ import annotations

import logging
from pathlib import Path

from factory.agents import compose_prompt
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig, load_config
from factory.gates.security import SecurityGate
from factory.github_api import GitHubClient, GitHubError, detect_repo
from factory.integrator import GitError, git
from factory.parsing import extract_json
from factory.sandbox import SandboxExecutor
from factory.selfheal.guardrails import HealLedger, make_signature

logger = logging.getLogger(__name__)

MAX_RUNS_PER_REVIEW = 5


class CIReviewer:
    def __init__(
        self,
        repo_root: Path,
        config: FactoryConfig,
        registry: BackendRegistry,
        github: GitHubClient,
        ledger: HealLedger | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.config = config
        self.registry = registry
        self.github = github
        self.ledger = ledger or HealLedger(
            repo_root / ".factory" / "factory.db",
            backoff_base_seconds=config.selfheal.backoff_base_seconds,
        )

    @classmethod
    def from_repo(cls, repo_root: Path) -> CIReviewer:
        config = load_config(repo_root)
        registry = BackendRegistry(config)
        github = GitHubClient(config.github.repo or detect_repo(str(repo_root)))
        return cls(repo_root, config, registry, github)

    # -- entry point ---------------------------------------------------------

    def review(self, run_id: int | None = None) -> list[str]:
        if not self.config.selfheal.ci_review:
            return ["ci_review disabled in factory.yaml"]
        if run_id is not None:
            runs = [self.github.get_run(run_id)]
        else:
            runs = self.github.list_failed_runs(limit=MAX_RUNS_PER_REVIEW)
        outcomes = []
        for run in runs:
            outcomes.append(self._review_run(run))
        return outcomes or ["no failed runs found"]

    def _review_run(self, run: dict) -> str:
        run_id = int(run["id"])
        workflow = str(run.get("name", "unknown"))
        logs = self.github.get_failed_logs(run_id)
        verdict = self._classify(run, logs)
        classification = verdict.get("classification", "infra")
        signature = str(
            verdict.get("signature") or make_signature(workflow, classification)
        )
        action = verdict.get("action", "escalate")
        summary = str(verdict.get("summary", ""))
        logger.info(
            "ci run classified",
            extra={"operation": "ci_review", "run_id": run_id,
                   "classification": classification, "action": action},
        )

        if action == "rerun" and classification == "transient":
            rerun_key = f"rerun:{signature}"
            if self.ledger.allowed(rerun_key, self.config.selfheal.max_reruns):
                self.ledger.record(rerun_key)
                self.github.rerun_failed_jobs(run_id)
                return f"run {run_id}: transient failure, rerunning ({summary})"
            action = "fix"  # rerun cap exhausted - treat as fixable

        if action == "fix":
            return self._fix(run, signature, verdict)

        self._escalate(run, classification, summary)
        return f"run {run_id}: escalated to humans ({classification}: {summary})"

    # -- steps ------------------------------------------------------------------

    def _classify(self, run: dict, logs: str) -> dict:
        prompt = compose_prompt(
            "ci_analyzer",
            task=(
                f"Workflow: {run.get('name')}\n"
                f"Branch: {run.get('head_branch')}\n"
                f"Event: {run.get('event')}\n"
                f"Attempt: {run.get('run_attempt')}\n\n"
                f"Failed job logs (tail):\n```\n{logs}\n```"
            ),
        )
        result = self.registry.run("ci_analyzer", prompt, cwd=str(self.repo_root))
        if not result.ok:
            return {"classification": "infra", "action": "escalate",
                    "summary": f"ci_analyzer failed: {result.error}"}
        try:
            return extract_json(result.output)
        except ValueError:
            return {"classification": "infra", "action": "escalate",
                    "summary": "unparseable ci_analyzer output"}

    def _fix(self, run: dict, signature: str, verdict: dict) -> str:
        run_id = int(run["id"])
        if not self.ledger.allowed(signature, self.config.selfheal.max_fix_attempts):
            self._escalate(run, str(verdict.get("classification", "")),
                           f"fix attempts exhausted for signature {signature}")
            return f"run {run_id}: fix attempts exhausted, escalated"
        attempt = self.ledger.record(signature)

        base = self.config.github.default_branch
        fix_branch = f"factory/ci-fix/{signature}-{attempt}"
        fix_path = self.repo_root / ".factory" / "worktrees" / f"ci-fix-{signature}"
        if fix_path.exists():
            self._remove_worktree(fix_path)
        fix_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            git(["worktree", "add", "-b", fix_branch, str(fix_path), base],
                cwd=self.repo_root)
        except GitError as exc:
            return f"run {run_id}: could not create fix worktree: {exc}"

        try:
            prompt = compose_prompt(
                "fixer",
                task=(
                    f"A GitHub Actions run failed.\n"
                    f"Summary: {verdict.get('summary', '')}\n"
                    f"Fix hint: {verdict.get('fix_hint', '')}\n\n"
                    "Fix the root cause in this checkout and commit with a "
                    "conventional fix: message."
                ),
                context=f"Failure signature: {signature} (attempt {attempt})",
            )
            result = self.registry.run("fixer", prompt, cwd=str(fix_path))
            if not result.ok:
                return f"run {run_id}: fixer agent failed: {result.error}"
            changed = git(["diff", "--name-only", f"{base}...{fix_branch}"],
                          cwd=self.repo_root).splitlines()
            if not changed:
                return f"run {run_id}: fixer produced no changes"

            workflow_edit = any(f.startswith(".github/workflows/") for f in changed)
            if workflow_edit and self.config.selfheal.workflow_edit_policy == "strict":
                security = SecurityGate(
                    self.config, SandboxExecutor(self.config.sandbox), self.registry
                ).run(fix_path, diff_text=git(
                    ["diff", f"{base}...{fix_branch}"], cwd=self.repo_root))
                if not security.passed:
                    return (f"run {run_id}: workflow-edit fix blocked by security gate: "
                            f"{security.summary()}")

            git(["push", "-u", "origin", fix_branch], cwd=self.repo_root, timeout=300)
            pr = self.github.create_pr(
                title=f"fix(ci): {verdict.get('summary', signature)[:80]}",
                head=fix_branch, base=base,
                body=(f"Automated CI fix for failed run "
                      f"[{run_id}]({run.get('html_url', '')}).\n\n"
                      f"Classification: {verdict.get('classification')}\n"
                      f"Signature: `{signature}` (attempt {attempt})\n\n"
                      f"{verdict.get('summary', '')}"),
            )
            number = int(pr["number"])
            labels = ["factory-selfheal"]
            if workflow_edit:
                labels.append(self.config.merge.needs_human_label)
            try:
                self.github.add_labels(number, labels)
            except GitHubError as exc:
                logger.warning("could not label fix PR",
                               extra={"operation": "ci_review", "error": str(exc)})
            return f"run {run_id}: fix PR #{number} opened ({fix_branch})"
        finally:
            self._remove_worktree(fix_path)

    def _escalate(self, run: dict, classification: str, summary: str) -> None:
        try:
            self.github.create_issue(
                title=f"CI failure needs human review: {run.get('name')} "
                      f"run {run.get('id')}",
                body=(f"Run: {run.get('html_url', '')}\n"
                      f"Classification: {classification}\n\n{summary}"),
                labels=[self.config.merge.needs_human_label, "factory-selfheal"],
            )
        except GitHubError as exc:
            logger.error("could not open escalation issue",
                         extra={"operation": "ci_review", "error": str(exc)})

    def _remove_worktree(self, path: Path) -> None:
        try:
            git(["worktree", "remove", "--force", str(path)], cwd=self.repo_root)
        except GitError:
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        try:
            git(["worktree", "prune"], cwd=self.repo_root)
        except GitError:
            pass
