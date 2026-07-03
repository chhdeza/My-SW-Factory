"""Mandatory human deploy gate.

A deploy first becomes a *pending request*; nothing executes until a human
approves it - via the dashboard button locally, or via the protected GitHub
Environment (required reviewers) in CI. State lives in
``.factory/deploy.json``; the runner refuses to execute anything that is not
approved.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from factory.config import FactoryConfig

logger = logging.getLogger(__name__)

MAX_HISTORY = 100


@dataclass
class DeployStatus:
    pending: str | None
    approved: bool
    history: list[dict] = field(default_factory=list)


class DeployGate:
    def __init__(self, repo_root: Path, config: FactoryConfig) -> None:
        self.config = config
        self._path = repo_root / ".factory" / "deploy.json"

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("deploy.json corrupt, resetting",
                               extra={"operation": "deploy_gate"})
        return {"pending": None, "history": []}

    def _save(self, data: dict) -> None:
        data["history"] = data.get("history", [])[-MAX_HISTORY:]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- lifecycle ------------------------------------------------------------

    def request(self, ref: str) -> str:
        data = self._load()
        if data["pending"] is not None:
            return f"deploy already pending for {data['pending']['ref']}"
        data["pending"] = {
            "ref": ref,
            "requested_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "approved": False,
            "approver": "",
        }
        self._save(data)
        logger.info("deploy requested", extra={"operation": "deploy_gate", "ref": ref})
        return f"deploy of {ref} pending human approval"

    def approve(self, approver: str) -> str:
        data = self._load()
        if data["pending"] is None:
            return "nothing pending"
        data["pending"]["approved"] = True
        data["pending"]["approver"] = approver
        data["pending"]["approved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        self._save(data)
        logger.info("deploy approved", extra={"operation": "deploy_gate",
                                              "approver": approver})
        return f"deploy of {data['pending']['ref']} approved by {approver}"

    def reject(self, approver: str) -> str:
        data = self._load()
        if data["pending"] is None:
            return "nothing pending"
        rejected = data["pending"]
        rejected["rejected_by"] = approver
        rejected["rejected_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        data["history"].append({"outcome": "rejected", **rejected})
        data["pending"] = None
        self._save(data)
        return f"deploy of {rejected['ref']} rejected"

    def take_approved(self) -> dict | None:
        """Consume the pending request iff approved (called by the runner)."""
        data = self._load()
        pending = data.get("pending")
        if not pending or not pending.get("approved"):
            return None
        data["history"].append({"outcome": "executed", **pending})
        data["pending"] = None
        self._save(data)
        return pending

    def status(self) -> DeployStatus:
        data = self._load()
        pending = data.get("pending")
        return DeployStatus(
            pending=pending["ref"] if pending else None,
            approved=bool(pending and pending.get("approved")),
            history=data.get("history", [])[-10:],
        )
