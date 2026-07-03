"""Agent role prompt loading and composition."""

from __future__ import annotations

from importlib import resources

ROLES = (
    "orchestrator",
    "coder",
    "conflict_resolver",
    "quality",
    "security",
    "reviewer",
    "fixer",
    "log_analyzer",
    "ci_analyzer",
)


def load_role_prompt(role: str) -> str:
    """Return the system prompt for a role from its packaged .md file."""
    if role not in ROLES:
        raise ValueError(f"unknown agent role: {role!r} (known: {', '.join(ROLES)})")
    return (
        resources.files("factory.agents").joinpath(f"{role}.md").read_text(encoding="utf-8")
    )


def compose_prompt(role: str, task: str, context: str = "") -> str:
    """Compose the full prompt: role instructions + optional context + task."""
    parts = [load_role_prompt(role)]
    if context:
        parts.append(f"## Context\n\n{context}")
    parts.append(f"## Task\n\n{task}")
    return "\n\n".join(parts)
