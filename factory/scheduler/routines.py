"""Routine actions: what a scheduled (or run-now) routine actually does.

Action types (factory.yaml ``schedules.<name>.action``):

- ``task``: run a full pipeline task (``params.text``).
- ``agent``: one prompt to one role (``ref`` = role, ``params.prompt``).
- ``gate``: run quality + security gates against the repo and report.
- ``report``: daily metrics summary.
- ``maintenance``: built-ins - ``log_review``, ``dependency_audit``,
  ``stale_branches``.
- ``command``: exact-argv command from ``scheduler.command_allowlist`` only.

Code-changing routines still flow through gates -> PR -> human deploy gate.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from factory.agents import ROLES, compose_prompt
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig, Routine, load_config
from factory.github_api import GitHubClient, GitHubError, detect_repo
from factory.sandbox import SandboxError, SandboxExecutor
from factory.state import StateStore

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 600
OUTPUT_TAIL_CHARS = 2_000


@dataclass
class RoutineOutcome:
    name: str
    ok: bool
    detail: str
    duration_seconds: float = 0.0

    def __str__(self) -> str:
        state = "ok" if self.ok else "FAILED"
        return f"routine {self.name}: {state} ({self.duration_seconds:.1f}s) - {self.detail}"


class RoutineExecutor:
    def __init__(self, repo_root: Path, config: FactoryConfig) -> None:
        self.repo_root = repo_root
        self.config = config
        self.registry = BackendRegistry(config)
        self.store = StateStore(repo_root / ".factory" / "factory.db")

    def _github(self) -> GitHubClient | None:
        try:
            return GitHubClient(self.config.github.repo or detect_repo(str(self.repo_root)))
        except GitHubError:
            return None

    def run(self, name: str) -> RoutineOutcome:
        routine = self.config.schedules.get(name)
        if routine is None:
            return RoutineOutcome(name, ok=False, detail=f"routine {name!r} not configured")
        if not routine.enabled:
            return RoutineOutcome(name, ok=False, detail="routine is disabled")
        started = time.monotonic()
        try:
            detail = self._dispatch(routine)
            ok = True
        except (SandboxError, ValueError, GitHubError) as exc:
            detail, ok = str(exc), False
        duration = time.monotonic() - started
        outcome = RoutineOutcome(name, ok=ok, detail=detail, duration_seconds=duration)
        self._record(outcome)
        logger.info(str(outcome), extra={"operation": "routine", "routine": name})
        return outcome

    def _record(self, outcome: RoutineOutcome) -> None:
        try:
            from factory.metrics.store import MetricsStore

            MetricsStore(self.repo_root / ".factory" / "factory.db").record_routine_run(
                outcome.name, outcome.ok, outcome.detail, outcome.duration_seconds
            )
        except ImportError:
            pass

    # -- dispatch -----------------------------------------------------------

    def _dispatch(self, routine: Routine) -> str:
        action = routine.action
        if action.type == "task":
            return self._action_task(str(action.params.get("text") or action.ref))
        if action.type == "agent":
            return self._action_agent(action.ref, str(action.params.get("prompt", "")))
        if action.type == "gate":
            return self._action_gate()
        if action.type == "report":
            return self._action_report()
        if action.type == "maintenance":
            return self._action_maintenance(action.ref)
        if action.type == "command":
            return self._action_command(action.params.get("argv", []))
        raise ValueError(f"unknown action type: {action.type}")

    def _action_task(self, text: str) -> str:
        if not text:
            raise ValueError("task action requires params.text")
        from factory.pipeline import Pipeline

        pipeline = Pipeline.from_repo(self.repo_root)
        task = pipeline.store.create_task(text.splitlines()[0][:120], text, source="routine")
        outcome = pipeline.run(task)
        return outcome.summary

    def _action_agent(self, role: str, prompt_text: str) -> str:
        if role not in ROLES:
            raise ValueError(f"agent action ref must be a role, got {role!r}")
        if not prompt_text:
            raise ValueError("agent action requires params.prompt")
        result = self.registry.run(
            role, compose_prompt(role, task=prompt_text), cwd=str(self.repo_root)
        )
        if not result.ok:
            raise ValueError(f"agent run failed: {result.error}")
        return result.output[-OUTPUT_TAIL_CHARS:]

    def _action_gate(self) -> str:
        from factory.gates.quality import QualityGate
        from factory.gates.security import SecurityGate

        sandbox = SandboxExecutor(self.config.sandbox)
        quality = QualityGate(self.config, sandbox, self.registry).run(self.repo_root)
        security = SecurityGate(self.config, sandbox, self.registry).run(self.repo_root)
        return f"{quality.summary()} | {security.summary()}"

    def _action_report(self) -> str:
        from factory.metrics.rollup import daily_summary_text

        return daily_summary_text(self.repo_root / ".factory" / "factory.db")

    def _action_maintenance(self, ref: str) -> str:
        if ref == "log_review":
            from factory.selfheal.log_review import LogReviewer

            reviewer = LogReviewer(self.repo_root, self.config, self.registry,
                                   self.store, self._github())
            return reviewer.run()
        if ref == "dependency_audit":
            return self._dependency_audit()
        if ref == "stale_branches":
            return self._stale_branches()
        raise ValueError(f"unknown maintenance action: {ref!r}")

    def _dependency_audit(self) -> str:
        sandbox = SandboxExecutor(self.config.sandbox)
        try:
            result = sandbox.run(
                ["pip-audit", "-f", "json", "--progress-spinner", "off"],
                cwd=self.repo_root,
            )
        except SandboxError as exc:
            return f"dependency audit skipped: {exc}"
        if result.ok:
            return "dependency audit: no known vulnerabilities"
        github = self._github()
        detail = result.stdout[-OUTPUT_TAIL_CHARS:]
        if github is not None:
            github.create_issue(
                "factory dependency audit found vulnerabilities",
                f"```\n{detail}\n```",
                labels=["factory-maintenance"],
            )
            return "dependency audit: vulnerabilities found, issue opened"
        return f"dependency audit: vulnerabilities found\n{detail}"

    def _stale_branches(self) -> str:
        from factory.integrator import GitError, git

        base = self.config.github.default_branch
        try:
            merged = git(["branch", "--merged", base, "--list", "factory/*",
                          "--format", "%(refname:short)"], cwd=self.repo_root).splitlines()
        except GitError as exc:
            return f"stale branch scan failed: {exc}"
        deleted = []
        for branch in (b.strip() for b in merged if b.strip()):
            try:
                git(["branch", "-D", branch], cwd=self.repo_root)
                deleted.append(branch)
            except GitError:
                continue
        return f"deleted {len(deleted)} merged factory branches"

    def _action_command(self, argv: list) -> str:
        argv = [str(a) for a in argv]
        if not argv:
            raise ValueError("command action requires params.argv")
        # Exact-argv allowlist: no shell, no interpolation, no prefix matching.
        if argv not in self.config.scheduler.command_allowlist:
            raise ValueError(
                f"command {argv!r} is not in scheduler.command_allowlist"
            )
        proc = subprocess.run(
            argv, cwd=str(self.repo_root), capture_output=True, text=True,
            timeout=COMMAND_TIMEOUT_SECONDS, shell=False,
        )
        tail = (proc.stdout + proc.stderr)[-OUTPUT_TAIL_CHARS:]
        if proc.returncode != 0:
            raise ValueError(f"command exited {proc.returncode}: {tail}")
        return tail or "command ok"


def run_routine_by_name(name: str, repo_root: Path | None = None) -> RoutineOutcome:
    root = repo_root or Path.cwd()
    return RoutineExecutor(root, load_config(root)).run(name)


def generate_routines_workflow(config: FactoryConfig) -> str:
    """Generate .github/workflows/routines.yml for scheduler.runner=ci.

    One workflow with every enabled cron; each routine job is guarded by
    ``github.event.schedule`` so a cron fires exactly its own routine.
    """
    enabled = {n: r for n, r in config.schedules.items() if r.enabled}
    lines = [
        "# Generated by `factory routine generate-ci`. Do not edit by hand.",
        "name: Factory routines",
        "",
        "on:",
        "  workflow_dispatch:",
        "    inputs:",
        "      routine:",
        "        description: Routine name to run",
        "        required: true",
    ]
    if enabled:
        lines.append("  schedule:")
        for routine in enabled.values():
            lines.append(f'    - cron: "{routine.cron}"')
    lines += [
        "",
        "permissions:",
        "  contents: write",
        "  pull-requests: write",
        "  issues: write",
        "  actions: read",
        "",
        "jobs:",
    ]
    for name, routine in enabled.items():
        lines += [
            f"  {name.replace('.', '-')}:",
            f"    if: github.event.schedule == '{routine.cron}' || "
            f"github.event.inputs.routine == '{name}'",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - uses: actions/setup-python@v5",
            "        with:",
            '          python-version: "3.12"',
            "      - run: pip install -e .",
            f"      - run: factory routine run {name}",
            "        env:",
            "          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
            "          CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}",
            "          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}",
        ]
    return "\n".join(lines) + "\n"
