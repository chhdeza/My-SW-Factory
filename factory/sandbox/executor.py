"""Pluggable execution sandbox.

All autonomous build/test/command execution goes through here. Two backends:

- ``docker``: runs the command in a throwaway container with the worktree
  mounted, no network by default, CPU/memory caps.
- ``subprocess``: confined fallback - command must be on the allowlist
  (argv[0] exact match, never a shell string), cwd is pinned to the worktree,
  and a hard timeout applies.

``mode: auto`` picks docker when the daemon responds, else subprocess.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from factory.config import SandboxConfig

logger = logging.getLogger(__name__)

DOCKER_PROBE_TIMEOUT = 5


class SandboxError(Exception):
    """Command refused or sandbox unavailable."""


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=DOCKER_PROBE_TIMEOUT,
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


class SandboxExecutor:
    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        if config.mode == "docker":
            if not docker_available():
                raise SandboxError("sandbox mode is 'docker' but the docker daemon is unavailable")
            self.mode = "docker"
        elif config.mode == "subprocess":
            self.mode = "subprocess"
        else:  # auto
            self.mode = "docker" if docker_available() else "subprocess"
        logger.info("sandbox ready", extra={"operation": "sandbox_init", "mode": self.mode})

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | Path,
        timeout_seconds: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run one command (exact argv, never a shell string) inside the sandbox."""
        if not argv:
            raise SandboxError("empty command")
        cwd = Path(cwd).resolve()
        if not cwd.is_dir():
            raise SandboxError(f"cwd does not exist: {cwd}")
        timeout = timeout_seconds or self.config.timeout_seconds
        if self.mode == "docker":
            return self._run_docker(argv, cwd, timeout, env)
        return self._run_subprocess(argv, cwd, timeout, env)

    # -- docker ---------------------------------------------------------------

    def _run_docker(
        self, argv: list[str], cwd: Path, timeout: int, env: dict[str, str] | None
    ) -> ExecResult:
        docker_argv = [
            "docker", "run", "--rm",
            "--network", self.config.network,
            "--cpus", str(self.config.cpu_limit),
            "--memory", f"{self.config.memory_limit_mb}m",
            "-v", f"{cwd}:/workspace",
            "-w", "/workspace",
        ]
        for key, value in (env or {}).items():
            docker_argv += ["-e", f"{key}={value}"]
        docker_argv += [self.config.docker_image, *argv]
        return self._exec(docker_argv, cwd=None, timeout=timeout, env=None)

    # -- confined subprocess -----------------------------------------------------

    def _run_subprocess(
        self, argv: list[str], cwd: Path, timeout: int, env: dict[str, str] | None
    ) -> ExecResult:
        executable = Path(argv[0]).name
        # Compare without Windows extension so 'python' matches python.exe.
        stem = executable.rsplit(".", 1)[0].lower()
        allowed = {a.lower() for a in self.config.command_allowlist}
        if stem not in allowed and executable.lower() not in allowed:
            raise SandboxError(
                f"command {argv[0]!r} is not in sandbox.command_allowlist "
                f"(allowed: {sorted(allowed)})"
            )
        return self._exec(argv, cwd=cwd, timeout=timeout, env=env)

    @staticmethod
    def _exec(
        argv: list[str], *, cwd: Path | None, timeout: int, env: dict[str, str] | None
    ) -> ExecResult:
        import os
        import sys

        merged_env = {**os.environ, **(env or {})}
        # Make gate tools resolvable regardless of the caller's PATH:
        # ~/.factory/bin holds downloaded binaries (factory tools install) and
        # the interpreter's script dir holds pip-installed ones.
        extra_dirs = [Path.home() / ".factory" / "bin", Path(sys.executable).parent]
        for directory in extra_dirs:
            if directory.is_dir():
                merged_env["PATH"] = (
                    str(directory) + os.pathsep + merged_env.get("PATH", "")
                )
        # Resolve the executable against the merged PATH ourselves - Windows
        # ignores the child env's PATH when locating the binary.
        resolved = shutil.which(argv[0], path=merged_env.get("PATH"))
        if resolved:
            argv = [resolved, *argv[1:]]
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=merged_env,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error(
                "sandbox command timed out",
                extra={"operation": "sandbox_run", "argv0": argv[0], "timeout": timeout},
            )
            return ExecResult(
                exit_code=-1,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
                timed_out=True,
            )
        except FileNotFoundError as exc:
            raise SandboxError(f"executable not found: {argv[0]}") from exc
        return ExecResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
