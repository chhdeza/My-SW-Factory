"""Daily log review routine: analyze recent activity, file improvement issues.

Packaged as a built-in scheduler maintenance action (``log_review``). Reads
recent task outcomes from the state store plus the tail of the factory log
file, asks the log_analyzer agent for findings, and opens GitHub issues for
actionable ones (or just logs them when GitHub isn't configured).
"""

from __future__ import annotations

import logging
from pathlib import Path

from factory.agents import compose_prompt
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig
from factory.github_api import GitHubClient, GitHubError
from factory.parsing import extract_json
from factory.state import StateStore

logger = logging.getLogger(__name__)

LOG_TAIL_CHARS = 20_000
MAX_ISSUES_PER_REVIEW = 3


class LogReviewer:
    def __init__(
        self,
        repo_root: Path,
        config: FactoryConfig,
        registry: BackendRegistry,
        store: StateStore,
        github: GitHubClient | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.config = config
        self.registry = registry
        self.store = store
        self.github = github

    def collect_evidence(self) -> str:
        """Recent task outcomes + log tail, bounded to keep the prompt small."""
        lines = ["## Recent tasks (up to 50)"]
        for task in self.store.list_tasks(limit=50):
            lines.append(
                f"- {task.id} [{task.status.value}] {task.title}"
                + (f" | error: {task.error[:200]}" if task.error else "")
            )
        log_file = self.repo_root / ".factory" / "factory.log"
        if log_file.exists():
            # Bounded read: only the tail, never the whole file.
            text = log_file.read_text(encoding="utf-8", errors="replace")
            lines.append("\n## Log tail\n" + text[-LOG_TAIL_CHARS:])
        return "\n".join(lines)

    def run(self) -> str:
        evidence = self.collect_evidence()
        prompt = compose_prompt("log_analyzer", task=evidence)
        result = self.registry.run("log_analyzer", prompt, cwd=str(self.repo_root))
        if not result.ok:
            return f"log review failed: {result.error}"
        try:
            verdict = extract_json(result.output)
        except ValueError:
            return "log review produced unparseable output"

        findings = verdict.get("findings", [])
        opened = 0
        for finding in findings:
            if not finding.get("open_issue") or opened >= MAX_ISSUES_PER_REVIEW:
                continue
            title = f"factory log review: {finding.get('signature', 'finding')}"
            body = (
                f"Kind: {finding.get('kind')}\n\n"
                f"Evidence:\n{finding.get('evidence', '')}\n\n"
                f"Proposal:\n{finding.get('proposal', '')}"
            )
            if self.github is not None:
                try:
                    self.github.create_issue(title, body, labels=["factory-improvement"])
                    opened += 1
                except GitHubError as exc:
                    logger.warning("could not open improvement issue",
                                   extra={"operation": "log_review", "error": str(exc)})
            else:
                logger.info("improvement finding (no GitHub configured): %s", title,
                            extra={"operation": "log_review"})
        healthy = bool(verdict.get("healthy", True))
        return (f"log review done: healthy={healthy}, findings={len(findings)}, "
                f"issues_opened={opened}")
