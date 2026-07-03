"""Git worktree lifecycle and serialized integration.

Each work unit gets its own worktree + branch under ``.factory/worktrees/``.
The integrator merges unit branches into a task integration branch one at a
time; conflicts are handed to the conflict-resolver agent up to a cap, then
escalated to a human.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from factory.agents import compose_prompt
from factory.backends.registry import BackendRegistry
from factory.config import FactoryConfig
from factory.state import StateStore, Task, UnitStatus, WorkUnit

logger = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 120
MAX_CONFLICT_ATTEMPTS = 2


class GitError(Exception):
    pass


class IntegrationError(Exception):
    """Integration failed after exhausting conflict-resolution attempts."""


def git(args: list[str], cwd: str | Path, timeout: int = GIT_TIMEOUT_SECONDS) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


class Integrator:
    def __init__(
        self,
        repo_root: str | Path,
        config: FactoryConfig,
        store: StateStore,
        registry: BackendRegistry,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        self.store = store
        self.registry = registry
        self.worktrees_dir = self.repo_root / ".factory" / "worktrees"

    # -- worktree lifecycle -------------------------------------------------

    def create_worktree(self, unit: WorkUnit, base_ref: str | None = None) -> Path:
        base = base_ref or self.config.github.default_branch
        branch = f"factory/{unit.task_id}/{unit.id}"
        path = self.worktrees_dir / unit.id
        if path.exists():
            self._remove_worktree_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        git(["worktree", "add", "-b", branch, str(path), base], cwd=self.repo_root)
        self.store.update_unit(unit.id, branch=branch, worktree_path=str(path))
        unit.branch, unit.worktree_path = branch, str(path)
        return path

    def remove_worktree(self, unit: WorkUnit) -> None:
        if unit.worktree_path:
            self._remove_worktree_path(Path(unit.worktree_path))
            self.store.update_unit(unit.id, worktree_path="")

    def _remove_worktree_path(self, path: Path) -> None:
        try:
            git(["worktree", "remove", "--force", str(path)], cwd=self.repo_root)
        except GitError:
            # Not a registered worktree (stale dir after crash) - delete directly.
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        git(["worktree", "prune"], cwd=self.repo_root)

    def reconcile_worktrees(self) -> list[str]:
        """Remove worktrees on disk that no live work unit owns (crash orphans)."""
        registered = self.store.registered_worktrees()
        removed: list[str] = []
        if self.worktrees_dir.exists():
            for child in self.worktrees_dir.iterdir():
                if str(child) not in registered:
                    self._remove_worktree_path(child)
                    removed.append(str(child))
        git(["worktree", "prune"], cwd=self.repo_root)
        if removed:
            logger.info(
                "pruned orphaned worktrees",
                extra={"operation": "reconcile", "count": len(removed)},
            )
        return removed

    # -- integration ----------------------------------------------------------

    def integrate(self, task: Task, units: list[WorkUnit], contract: str) -> str:
        """Serially merge unit branches into a task integration branch.

        Returns the integration branch name. Raises IntegrationError when a
        conflict survives the resolver cap.
        """
        # Unit branches live at factory/<task>/<unit>, so the integration ref
        # needs its own leaf name to avoid a ref directory/file collision.
        integration_branch = f"factory/{task.id}/integration"
        base = self.config.github.default_branch
        existing = git(["branch", "--list", integration_branch], cwd=self.repo_root)
        if not existing:
            git(["branch", integration_branch, base], cwd=self.repo_root)

        # A dedicated worktree keeps integration off the user's checkout.
        integ_path = self.worktrees_dir / f"integration-{task.id}"
        if integ_path.exists():
            self._remove_worktree_path(integ_path)
        integ_path.parent.mkdir(parents=True, exist_ok=True)
        git(["worktree", "add", str(integ_path), integration_branch], cwd=self.repo_root)

        try:
            for unit in units:
                if not unit.branch:
                    continue
                self._merge_unit(integ_path, unit, contract)
                self.store.update_unit(unit.id, status=UnitStatus.INTEGRATED)
        finally:
            self._remove_worktree_path(integ_path)
        return integration_branch

    def _merge_unit(self, integ_path: Path, unit: WorkUnit, contract: str) -> None:
        try:
            git(["merge", "--no-ff", "--no-edit", unit.branch], cwd=integ_path)
            return
        except GitError as first_error:
            logger.warning(
                "merge conflict, dispatching resolver",
                extra={"operation": "integrate", "unit": unit.id, "branch": unit.branch},
            )
            last_error = first_error

        for attempt in range(1, MAX_CONFLICT_ATTEMPTS + 1):
            conflicted = git(
                ["diff", "--name-only", "--diff-filter=U"], cwd=integ_path
            ).splitlines()
            if not conflicted:
                # Resolver (or a previous pass) already completed the merge.
                return
            prompt = compose_prompt(
                "conflict_resolver",
                task=(
                    f"Resolve the merge conflicts in this repository checkout. Conflicted "
                    f"files: {', '.join(conflicted)}. Complete the merge with "
                    f"`git add <files>` and `git commit --no-edit`."
                ),
                context=f"Shared interface contract:\n{contract}",
            )
            result = self.registry.run("conflict_resolver", prompt, cwd=str(integ_path))
            if result.ok and not git(
                ["diff", "--name-only", "--diff-filter=U"], cwd=integ_path
            ):
                # Verify the merge actually concluded (no MERGE_HEAD left).
                try:
                    git(["rev-parse", "--verify", "MERGE_HEAD"], cwd=integ_path)
                    git(["commit", "--no-edit"], cwd=integ_path)
                except GitError:
                    pass  # merge already committed
                return
            logger.warning(
                "conflict resolution attempt failed",
                extra={"operation": "integrate", "unit": unit.id, "attempt": attempt},
            )
        git(["merge", "--abort"], cwd=integ_path)
        raise IntegrationError(
            f"unit {unit.id} ({unit.branch}) conflicts unresolved after "
            f"{MAX_CONFLICT_ATTEMPTS} resolver attempts: {last_error}"
        )
