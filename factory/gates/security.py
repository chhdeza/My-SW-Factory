"""Security gate: bandit, semgrep, gitleaks, dependency audit, LLM review.

Optional MCP servers (SonarQube, GitGuardian, Wiz, ...) declared in
factory.yaml ``mcps:`` are passed to the LLM security reviewer so it can query
them; they are off by default. Thresholds follow CONTRACT.md.
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
_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def severity_at_least(severity: str, threshold: str) -> bool:
    return _SEVERITY_ORDER.get(severity.lower(), 0) >= _SEVERITY_ORDER.get(
        threshold.lower(), 2
    )


class SecurityGate:
    def __init__(
        self, config: FactoryConfig, sandbox: SandboxExecutor, registry: BackendRegistry
    ) -> None:
        self.config = config
        self.sandbox = sandbox
        self.registry = registry

    def run(self, worktree: str | Path, diff_text: str = "") -> GateReport:
        report = GateReport(gate="security")
        gate_cfg = self.config.gates.security
        if not gate_cfg.enabled:
            report.checks.append(CheckResult("security", passed=True, skipped=True,
                                             details="gate disabled"))
            return report

        worktree = Path(worktree)
        profiles = {p.name for p in detect_profiles(worktree, self.config.gates)}

        if gate_cfg.bandit.enabled and "python" in profiles:
            report.checks.append(self._bandit(worktree))
        if gate_cfg.semgrep.enabled:
            report.checks.append(self._semgrep(worktree))
        if gate_cfg.gitleaks.enabled:
            report.checks.append(self._gitleaks(worktree))
        if gate_cfg.dep_audit.enabled:
            if "python" in profiles:
                report.checks.append(self._pip_audit(worktree))
            if "node" in profiles:
                report.checks.append(self._npm_audit(worktree))
        if gate_cfg.llm_review.enabled and diff_text:
            report.checks.append(self._llm_review(worktree, diff_text))
        logger.info(report.summary(), extra={"operation": "security_gate"})
        return report

    def _bandit(self, worktree: Path) -> CheckResult:
        try:
            result = self.sandbox.run(
                ["bandit", "-r", ".", "-f", "json", "-q", "-x", "./.venv,./tests"],
                cwd=worktree,
            )
        except SandboxError as exc:
            return CheckResult("bandit", passed=False, skipped=True, details=str(exc))
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return CheckResult("bandit", passed=False, skipped=True,
                               details="unparseable bandit output")
        threshold = self.config.gates.security.bandit.fail_level
        blocking = [
            issue for issue in data.get("results", [])
            if severity_at_least(issue.get("issue_severity", "low"), threshold)
        ]
        detail = "; ".join(
            f"{i.get('filename')}:{i.get('line_number')} {i.get('test_id')}"
            for i in blocking[:10]
        )
        return CheckResult("bandit", passed=not blocking,
                           details=detail or f"no findings at/above {threshold}")

    def _semgrep(self, worktree: Path) -> CheckResult:
        try:
            result = self.sandbox.run(
                ["semgrep", "scan", "--config", "auto", "--json", "--quiet"], cwd=worktree
            )
        except SandboxError as exc:
            return CheckResult("semgrep", passed=False, skipped=True, details=str(exc))
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return CheckResult("semgrep", passed=False, skipped=True,
                               details="unparseable semgrep output")
        fail_sev = self.config.gates.security.semgrep.fail_severity.upper()
        blocking = [
            r for r in data.get("results", [])
            if r.get("extra", {}).get("severity", "").upper() == fail_sev
            or (fail_sev == "ERROR" and r.get("extra", {}).get("severity") == "ERROR")
        ]
        detail = "; ".join(
            f"{r.get('path')}: {r.get('check_id')}" for r in blocking[:10]
        )
        return CheckResult("semgrep", passed=not blocking,
                           details=detail or f"no findings at {fail_sev}")

    def _gitleaks(self, worktree: Path) -> CheckResult:
        report_path = worktree / ".factory-gitleaks.json"
        try:
            self.sandbox.run(
                ["gitleaks", "detect", "--no-banner", "--source", ".",
                 "--report-format", "json", "--report-path", report_path.name],
                cwd=worktree,
            )
        except SandboxError as exc:
            return CheckResult("gitleaks", passed=False, skipped=True, details=str(exc))
        findings: list = []
        if report_path.exists():
            try:
                findings = json.loads(report_path.read_text(encoding="utf-8") or "[]")
            except json.JSONDecodeError:
                findings = []
            finally:
                report_path.unlink(missing_ok=True)
        max_allowed = self.config.gates.security.gitleaks.max_findings
        detail = "; ".join(
            f"{f.get('File')}:{f.get('StartLine')} {f.get('RuleID')}" for f in findings[:10]
        )
        return CheckResult("gitleaks", passed=len(findings) <= max_allowed,
                           details=detail or "no secrets detected")

    def _pip_audit(self, worktree: Path) -> CheckResult:
        try:
            result = self.sandbox.run(["pip-audit", "-f", "json", "--progress-spinner", "off"],
                                      cwd=worktree)
        except SandboxError as exc:
            return CheckResult("pip-audit", passed=False, skipped=True, details=str(exc))
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return CheckResult("pip-audit", passed=False, skipped=True,
                               details="unparseable pip-audit output")
        vulnerable = [
            dep for dep in data.get("dependencies", []) if dep.get("vulns")
        ]
        detail = "; ".join(f"{d.get('name')} {d.get('version')}" for d in vulnerable[:10])
        return CheckResult("pip-audit", passed=not vulnerable,
                           details=detail or "no known vulnerabilities")

    def _npm_audit(self, worktree: Path) -> CheckResult:
        threshold = self.config.gates.security.dep_audit.fail_severity
        try:
            result = self.sandbox.run(
                ["npm", "audit", "--json", f"--audit-level={threshold}"], cwd=worktree
            )
        except SandboxError as exc:
            return CheckResult("npm-audit", passed=False, skipped=True, details=str(exc))
        return CheckResult("npm-audit", passed=result.ok,
                           details=(result.stdout + result.stderr)[-1000:])

    def _llm_review(self, worktree: Path, diff_text: str) -> CheckResult:
        mcp_servers = {
            name: {"transport": server.transport, "command": server.command,
                   "url": server.url, "env": server.resolved_env()}
            for name, server in self.config.mcps.items()
            if server.enabled
        }
        prompt = compose_prompt(
            "security",
            task="Review this diff:\n\n```diff\n"
            + diff_text[:MAX_DIFF_CHARS_FOR_REVIEW]
            + "\n```",
        )
        result = self.registry.run(
            "security", prompt, cwd=str(worktree), mcp_servers=mcp_servers or None
        )
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
