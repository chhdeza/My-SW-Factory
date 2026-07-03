"""Shared fixtures."""

import subprocess
from pathlib import Path

import pytest


def run_git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A fresh git repo with one commit on `main`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(["init", "-b", "main"], repo)
    run_git(["config", "user.email", "factory@test.local"], repo)
    run_git(["config", "user.name", "Factory Test"], repo)
    (repo / "README.md").write_text("# sample\n", encoding="utf-8")
    run_git(["add", "."], repo)
    run_git(["commit", "-m", "chore: initial"], repo)
    return repo
