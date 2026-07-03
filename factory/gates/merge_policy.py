"""Risk-based merge policy.

Low-risk changes auto-merge once gates + CI pass. High-risk changes - workflow
edits, dependency upgrades, security-flagged findings, migrations, large diffs
- are held with a needs-human-review label. Deploy stays human-gated
regardless of this policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from factory.config import MergeConfig

_DEPENDENCY_MANIFESTS = (
    "pyproject.toml",
    "requirements.txt",
    "requirements.in",
    "setup.py",
    "poetry.lock",
    "uv.lock",
    "Pipfile",
    "Pipfile.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
)

_MIGRATION_RE = re.compile(r"(^|/)(migrations?|alembic)(/|$)", re.IGNORECASE)


@dataclass
class RiskAssessment:
    risk: str  # "low" | "high"
    reasons: list[str] = field(default_factory=list)

    @property
    def high(self) -> bool:
        return self.risk == "high"


def assess_risk(
    changed_files: list[str],
    diff_lines: int,
    rules: MergeConfig,
    *,
    security_flagged: bool = False,
    reviewer_risk: str = "low",
) -> RiskAssessment:
    reasons: list[str] = []
    high = rules.high_risk

    if high.workflow_edits and any(
        f.startswith(".github/workflows/") for f in changed_files
    ):
        reasons.append("edits GitHub Actions workflows")
    if high.dependency_upgrades and any(
        f.split("/")[-1] in _DEPENDENCY_MANIFESTS for f in changed_files
    ):
        reasons.append("changes dependency manifests")
    if high.migrations and any(_MIGRATION_RE.search(f) for f in changed_files):
        reasons.append("contains database migrations")
    if diff_lines > high.max_diff_lines:
        reasons.append(f"large diff ({diff_lines} lines > {high.max_diff_lines})")
    if high.security_flagged and security_flagged:
        reasons.append("security gate raised findings")
    if reviewer_risk == "high":
        reasons.append("reviewer agent assessed high risk")

    return RiskAssessment(risk="high" if reasons else "low", reasons=reasons)


def merge_decision(assessment: RiskAssessment, rules: MergeConfig) -> str:
    """Return 'auto_merge' or 'needs_human'."""
    if assessment.high or not rules.auto_merge_low_risk:
        return "needs_human"
    return "auto_merge"
