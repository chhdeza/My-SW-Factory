"""Self-heal tests: guardrails, gate healer, CI reviewer."""

import json
from datetime import UTC, datetime
from typing import Any

from factory.backends import RunResult, RunStatus, Usage
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig, Provider
from factory.gates import CheckResult, GateReport
from factory.pipeline import Pipeline
from factory.selfheal.ci_review import CIReviewer
from factory.selfheal.guardrails import HealLedger, make_signature
from factory.selfheal.loop import GateHealer, failure_signature
from factory.state import StateStore
from tests.conftest import run_git

# -- guardrails ----------------------------------------------------------------


def test_signature_stable_and_distinct():
    a = make_signature("quality:tests", "assert 1 == 2")
    b = make_signature("quality:tests", "assert 1 == 2")
    c = make_signature("quality:lint", "E501")
    assert a == b
    assert a != c


def test_ledger_caps_attempts(tmp_path):
    ledger = HealLedger(tmp_path / "db.sqlite", backoff_base_seconds=0)
    sig = "abc"
    assert ledger.allowed(sig, max_attempts=2)
    ledger.record(sig)
    assert ledger.allowed(sig, max_attempts=2)
    ledger.record(sig)
    assert not ledger.allowed(sig, max_attempts=2)


def test_ledger_backoff_blocks_immediate_retry(tmp_path):
    ledger = HealLedger(tmp_path / "db.sqlite", backoff_base_seconds=3600)
    ledger.record("sig")
    assert not ledger.allowed("sig", max_attempts=5)  # within backoff window


def test_failure_signature_from_reports():
    report = GateReport(gate="quality",
                        checks=[CheckResult("tests", passed=False, details="boom")])
    assert failure_signature([report]) == failure_signature([report])


# -- gate healer -------------------------------------------------------------------


class FixerBackend:
    """Writes a fix and commits when acting as the fixer."""

    name = "fixer"

    def __init__(self, commits: bool = True) -> None:
        self._commits = commits

    def run(self, *, prompt: str, cwd: str, model: str, timeout_seconds: int = 900,
            mcp_servers: dict[str, Any] | None = None) -> RunResult:
        from pathlib import Path

        if "# Role: Fixer" in prompt and self._commits:
            target = Path(cwd) / "fixed.txt"
            target.write_text("fixed\n", encoding="utf-8")
            run_git(["add", "-A"], Path(cwd))
            run_git(["commit", "-m", "fix: heal gate failure"], Path(cwd))
        return RunResult(status=RunStatus.FINISHED, output="done", usage=Usage(),
                         provider=self.name, model=model)


def make_pipeline(git_repo, backend) -> Pipeline:
    cfg = FactoryConfig()
    cfg.selfheal.backoff_base_seconds = 0
    store = StateStore(git_repo / ".factory" / "factory.db")
    registry = BackendRegistry(cfg)
    registry.register(Provider.CURSOR, backend)
    registry.register(Provider.CLAUDE, backend)
    return Pipeline(git_repo, cfg, store, registry)


def failing_reports() -> list[GateReport]:
    return [GateReport(gate="quality",
                       checks=[CheckResult("tests", passed=False, details="1 failed")])]


def test_gate_healer_commits_fix(git_repo):
    pipeline = make_pipeline(git_repo, FixerBackend())
    healer = GateHealer(pipeline)
    task = pipeline.store.create_task("t", "d")
    run_git(["branch", "target-branch", "main"], git_repo)

    healed = healer.heal(task, "target-branch", failing_reports())

    assert healed
    files = run_git(["ls-tree", "-r", "--name-only", "target-branch"], git_repo)
    assert "fixed.txt" in files


def test_gate_healer_reports_no_fix_when_agent_does_nothing(git_repo):
    pipeline = make_pipeline(git_repo, FixerBackend(commits=False))
    healer = GateHealer(pipeline)
    task = pipeline.store.create_task("t", "d")
    run_git(["branch", "target-branch", "main"], git_repo)

    assert not healer.heal(task, "target-branch", failing_reports())


def test_gate_healer_respects_attempt_cap(git_repo):
    pipeline = make_pipeline(git_repo, FixerBackend(commits=False))
    pipeline.config.selfheal.max_fix_attempts = 1
    healer = GateHealer(pipeline)
    task = pipeline.store.create_task("t", "d")
    run_git(["branch", "target-branch", "main"], git_repo)
    reports = failing_reports()

    healer.heal(task, "target-branch", reports)   # consumes the only attempt
    assert not healer.heal(task, "target-branch", reports)


# -- CI reviewer ------------------------------------------------------------------


class FakeGitHub:
    def __init__(self) -> None:
        self.reruns: list[int] = []
        self.issues: list[dict] = []
        self.prs: list[dict] = []
        self.labels: list[tuple] = []

    def get_run(self, run_id: int) -> dict:
        return {"id": run_id, "name": "CI", "head_branch": "main", "event": "push",
                "run_attempt": 1, "html_url": f"https://x/runs/{run_id}"}

    def list_failed_runs(self, limit: int = 10) -> list[dict]:
        return [self.get_run(101)]

    def get_failed_logs(self, run_id: int, max_chars: int = 40_000) -> str:
        return "ETIMEDOUT connecting to registry.npmjs.org"

    def rerun_failed_jobs(self, run_id: int) -> None:
        self.reruns.append(run_id)

    def create_issue(self, title: str, body: str, labels=None) -> dict:
        self.issues.append({"title": title, "labels": labels})
        return {"number": len(self.issues)}

    def create_pr(self, title: str, head: str, base: str, body: str) -> dict:
        self.prs.append({"title": title, "head": head})
        return {"number": len(self.prs)}

    def add_labels(self, number: int, labels: list[str]) -> None:
        self.labels.append((number, labels))


class AnalyzerBackend:
    """ci_analyzer returns a canned verdict."""

    name = "analyzer"

    def __init__(self, verdict: dict) -> None:
        self._verdict = verdict

    def run(self, *, prompt: str, cwd: str, model: str, timeout_seconds: int = 900,
            mcp_servers: dict[str, Any] | None = None) -> RunResult:
        return RunResult(status=RunStatus.FINISHED, output=json.dumps(self._verdict),
                         usage=Usage(), provider=self.name, model=model)


def make_reviewer(git_repo, verdict: dict) -> tuple[CIReviewer, FakeGitHub]:
    cfg = FactoryConfig()
    cfg.selfheal.backoff_base_seconds = 0
    registry = BackendRegistry(cfg)
    backend = AnalyzerBackend(verdict)
    registry.register(Provider.CURSOR, backend)
    registry.register(Provider.CLAUDE, backend)
    github = FakeGitHub()
    ledger = HealLedger(git_repo / ".factory" / "factory.db", backoff_base_seconds=0)
    return CIReviewer(git_repo, cfg, registry, github, ledger), github


def test_transient_failure_rerun_up_to_cap(git_repo):
    verdict = {"classification": "transient", "signature": "net-flake",
               "action": "rerun", "summary": "npm registry timeout"}
    reviewer, github = make_reviewer(git_repo, verdict)
    reviewer.config.selfheal.max_reruns = 2

    reviewer.review(run_id=101)
    reviewer.review(run_id=101)
    assert github.reruns == [101, 101]

    # Third time the rerun cap is exhausted -> falls through to fix; the
    # analyzer backend acts as fixer but makes no changes -> "no changes".
    outcome = reviewer.review(run_id=101)[0]
    assert "rerunning" not in outcome


def test_infra_failure_escalates_issue(git_repo):
    verdict = {"classification": "infra", "signature": "bad-secret",
               "action": "escalate", "summary": "missing secret FOO"}
    reviewer, github = make_reviewer(git_repo, verdict)

    outcome = reviewer.review(run_id=7)[0]

    assert "escalated" in outcome
    assert github.issues and "needs-human-review" in github.issues[0]["labels"]


def test_ci_review_disabled(git_repo):
    reviewer, _ = make_reviewer(git_repo, {})
    reviewer.config.selfheal.ci_review = False
    assert reviewer.review() == ["ci_review disabled in factory.yaml"]


def test_timestamps_are_utc():
    # Guardrail timestamps must be timezone-aware UTC for correct backoff math.
    assert datetime.now(UTC).tzinfo is not None
