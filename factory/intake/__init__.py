"""Task intake: CLI, GitHub issues, webhook, and self-heal signals.

All intake paths normalize into the same ``Task`` row the orchestrator
consumes.
"""

from __future__ import annotations

from factory.github_api import GitHubClient
from factory.state import StateStore, Task

MAX_TITLE_LEN = 120


def task_from_text(store: StateStore, text: str, source: str = "cli") -> Task:
    text = text.strip()
    if not text:
        raise ValueError("task description is empty")
    title = text.splitlines()[0][:MAX_TITLE_LEN]
    return store.create_task(title=title, description=text, source=source)


def task_from_issue(store: StateStore, client: GitHubClient, issue_number: int) -> Task:
    issue = client.get_issue(issue_number)
    title = str(issue.get("title", f"issue #{issue_number}"))[:MAX_TITLE_LEN]
    body = str(issue.get("body") or "")
    description = f"GitHub issue #{issue_number}: {title}\n\n{body}"
    return store.create_task(title=title, description=description, source="issue")
