"""Quality gate: ruff, pytest, coverage, actionlint, and LLM diff review.

Thresholds come from CONTRACT.md via factory.yaml. A tool that is enabled but
not installed produces a SKIP (with details), not a silent pass - the gate
report always says what actually ran.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from factory.agents import compose_prompt
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig
from factory.gates import CheckResult, GateReport
from factory.gates.profiles import detect_profiles
from factory.parsing import extract_json
from factory.sandbox import SandboxError, SandboxExecutor

logger = logging.getLogger(__name__)

MAX_DIFF_CHARS_FOR_REVIEW = 60_000


class QualityGate:
    def __init__(
        self, config: FactoryConfig, sandbox: SandboxExecutor, registry: BackendRegistry
    ) -> None:
        self.config = config
        self.sandbox = sandbox
        self.registry = registry

    def run(
        self,
        worktree: str | Path,
        diff_text: str = "",
        changed_files: list[str] | None = None,
    ) -> GateReport:
        report = GateReport(gate="quality")
        gate_cfg = self.config.gates.quality
        if not gate_cfg.enabled:
            report.checks.append(CheckResult("quality", passed=True, skipped=True,
                                             details="gate disabled"))
            return report

        worktree = Path(worktree)
        profiles = detect_profiles(worktree, self.config.gates)

        if gate_cfg.ruff.enabled:
            report.checks.append(self._lint(worktree, profiles))
        if gate_cfg.pytest.enabled:
            report.checks.append(self._tests(worktree, profiles))
        if gate_cfg.coverage.enabled:
            report.checks.append(self._coverage(worktree, profiles))
        if gate_cfg.actionlint.enabled:
            report.checks.append(self._actionlint(worktree, changed_files or []))
        if gate_cfg.llm_review.enabled and diff_text:
            report.checks.append(self._llm_review(worktree, diff_text))
        logger.info(report.summary(), extra={"operation": "quality_gate"})
        return report

    def _run_tool(self, name: str, argv: list[str], worktree: Path) -> CheckResult | None:
        """Run a tool; None means caller decides, SKIP result when tool missing."""
        try:
            result = self.sandbox.run(argv, cwd=worktree)
        except SandboxError as exc:
            return CheckResult(name, passed=False, skipped=True, details=str(exc))
        tail = (result.stdout + result.stderr)[-2000:]
        return CheckResult(name, passed=result.ok, details=tail)

    def _lint(self, worktree: Path, profiles) -> CheckResult:
        results = []
        for profile in profiles:
            if not profile.lint:
                continue
            check = self._run_tool(f"lint[{profile.name}]", profile.lint, worktree)
            results.append(check)
        if not results:
            return CheckResult("lint", passed=True, skipped=True, details="no profile detected")
        failed = [r for r in results if not r.passed and not r.skipped]
        merged = failed[0] if failed else results[0]
        return CheckResult("lint", passed=not failed,
                           skipped=all(r.skipped for r in results), details=merged.details)

    def _tests(self, worktree: Path, profiles) -> CheckResult:
        results = []
        for profile in profiles:
            if not profile.test:
                continue
            results.append(self._run_tool(f"test[{profile.name}]", profile.test, worktree))
        if not results:
            return CheckResult("tests", passed=True, skipped=True, details="no profile detected")
        failed = [r for r in results if not r.passed and not r.skipped]
        merged = failed[0] if failed else results[0]
        return CheckResult("tests", passed=not failed,
                           skipped=all(r.skipped for r in results), details=merged.details)

    def _coverage(self, worktree: Path, profiles) -> CheckResult:
        if not any(p.name == "python" for p in profiles):
            return CheckResult("coverage", passed=True, skipped=True,
                               details="python profile not detected")
        argv = ["python", "-m", "pytest", "-q", "--cov=.", "--cov-report=json"]
        try:
            result = self.sandbox.run(argv, cwd=worktree)
        except SandboxError as exc:
            return CheckResult("coverage", passed=False, skipped=True, details=str(exc))
        cov_file = worktree / "coverage.json"
        if not result.ok or not cov_file.exists():
            return CheckResult("coverage", passed=False, skipped=True,
                               details="pytest-cov unavailable or tests failed")
        try:
            percent = json.loads(cov_file.read_text(encoding="utf-8"))["totals"][
                "percent_covered"
            ]
        except (json.JSONDecodeError, KeyError) as exc:
            return CheckResult("coverage", passed=False, skipped=True,
                               details=f"unreadable coverage.json: {exc}")
        minimum = self.config.gates.quality.coverage.min_percent
        return CheckResult(
            "coverage",
            passed=percent >= minimum,
            details=f"{percent:.1f}% (minimum {minimum:.0f}%)",
        )

    def _actionlint(self, worktree: Path, changed_files: list[str]) -> CheckResult:
        workflow_changes = [
            f for f in changed_files if f.startswith(".github/workflows/")
        ]
        if not workflow_changes:
            return CheckResult("actionlint", passed=True, skipped=True,
                               details="no workflow changes")
        check = self._run_tool("actionlint", ["actionlint", *workflow_changes], worktree)
        check.name = "actionlint"
        return check

    def _llm_review(self, worktree: Path, diff_text: str) -> CheckResult:
        prompt = compose_prompt(
            "quality",
            task="Review this diff:\n\n```diff\n"
            + diff_text[:MAX_DIFF_CHARS_FOR_REVIEW]
            + "\n```",
        )
        result = self.registry.run("quality", prompt, cwd=str(worktree))
        if not result.ok:
            return CheckResult("llm_review", passed=False, skipped=True,
                               details=f"review agent failed: {result.error}")
        try:
            verdict = extract_json(result.output)
        except ValueError as exc:
            return CheckResult("llm_review", passed=False, skipped=True, details=str(exc))
        findings = verdict.get("findings", [])
        detail = "; ".join(
            f"[{f.get('severity')}] {f.get('file')}: {f.get('issue')}" for f in findings[:10]
        )
        return CheckResult("llm_review", passed=verdict.get("verdict") == "pass",
                           details=detail or "no findings")
