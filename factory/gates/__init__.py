"""On-demand quality and security gates with explicit thresholds."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    name: str
    passed: bool
    skipped: bool = False
    details: str = ""


@dataclass
class GateReport:
    gate: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed or c.skipped for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and not c.skipped]

    def summary(self) -> str:
        parts = []
        for check in self.checks:
            mark = "SKIP" if check.skipped else ("PASS" if check.passed else "FAIL")
            parts.append(f"{mark} {check.name}")
        return f"{self.gate}: " + ", ".join(parts)
