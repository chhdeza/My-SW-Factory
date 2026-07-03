"""Pluggable deploy execution - runs only after human approval.

Hooks keep the scaffold generic:

- ``noop``: logs and succeeds (default; exercises the gate without infra).
- ``cloudrun``: OpenTofu apply of infra/cloudrun (GCP Cloud Run, free tier).
- ``compose_vm``: OpenTofu apply of infra/vm-compose (free-tier VM + compose).
- any dotted path ``pkg.module:function`` taking (ref, config) -> str.
"""

from __future__ import annotations

import importlib
import logging
import subprocess
from pathlib import Path

from factory.config import FactoryConfig
from factory.deploy.gate import DeployGate

logger = logging.getLogger(__name__)

TOFU_TIMEOUT_SECONDS = 1800


class DeployError(Exception):
    pass


def _run_tofu(module_dir: Path, variables: dict[str, str]) -> str:
    if not module_dir.is_dir():
        raise DeployError(f"infra module not found: {module_dir}")
    var_args: list[str] = []
    for key, value in variables.items():
        var_args += ["-var", f"{key}={value}"]
    for args in (["init", "-input=false"],
                 ["apply", "-auto-approve", "-input=false", *var_args]):
        proc = subprocess.run(
            ["tofu", *args], cwd=str(module_dir), capture_output=True, text=True,
            timeout=TOFU_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            raise DeployError(f"tofu {args[0]} failed: {proc.stderr[-2000:]}")
    return f"tofu apply ok in {module_dir.name}"


class DeployRunner:
    def __init__(self, repo_root: Path, config: FactoryConfig) -> None:
        self.repo_root = repo_root
        self.config = config
        self.gate = DeployGate(repo_root, config)

    def execute(self) -> str:
        """Execute the pending deploy - refuses without human approval."""
        approved = self.gate.take_approved()
        if approved is None:
            raise DeployError(
                "no approved deploy pending - approval is mandatory (dashboard or "
                "GitHub Environment)"
            )
        ref = str(approved["ref"])
        hook = self.config.deploy.hook
        logger.info("executing deploy", extra={"operation": "deploy", "ref": ref,
                                               "hook": hook})
        if hook == "noop":
            return f"noop deploy of {ref} completed (no infrastructure configured)"
        if hook == "cloudrun":
            return _run_tofu(self.repo_root / "infra" / "cloudrun",
                             {**self.config.deploy.cloudrun, "app_ref": ref})
        if hook == "compose_vm":
            return _run_tofu(self.repo_root / "infra" / "vm-compose",
                             {**self.config.deploy.compose_vm, "app_ref": ref})
        if ":" in hook:
            module_name, func_name = hook.split(":", 1)
            module = importlib.import_module(module_name)
            func = getattr(module, func_name)
            return str(func(ref, self.config))
        raise DeployError(f"unknown deploy hook: {hook!r}")
