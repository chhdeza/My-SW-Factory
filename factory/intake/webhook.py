"""Optional GitHub webhook intake (issue labeled -> factory task).

Run with: ``uvicorn factory.intake.webhook:app``. Configure a GitHub webhook
for ``issues`` events with a secret in ``FACTORY_WEBHOOK_SECRET``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from factory.config import load_config
from factory.intake import task_from_text
from factory.state import StateStore

logger = logging.getLogger(__name__)

TRIGGER_LABEL = os.environ.get("FACTORY_TRIGGER_LABEL", "factory")

app = FastAPI(title="factory-intake")


class IssuePayload(BaseModel):
    action: str
    issue: dict
    label: dict | None = None


def _verify_signature(secret: str, body: bytes, signature: str | None) -> None:
    if not secret:
        raise HTTPException(status_code=503, detail="FACTORY_WEBHOOK_SECRET not configured")
    if not signature:
        raise HTTPException(status_code=401, detail="missing signature")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid signature")


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str = Header(default=""),
):
    body = await request.body()
    _verify_signature(os.environ.get("FACTORY_WEBHOOK_SECRET", ""), body, x_hub_signature_256)
    if x_github_event != "issues":
        return {"status": "ignored", "reason": f"event {x_github_event!r} not handled"}

    payload = IssuePayload.model_validate_json(body)
    if payload.action != "labeled" or (payload.label or {}).get("name") != TRIGGER_LABEL:
        return {"status": "ignored", "reason": "not a trigger label event"}

    config = load_config()
    store = StateStore(config.state_dir / "factory.db")
    issue = payload.issue
    text = (
        f"GitHub issue #{issue.get('number')}: {issue.get('title', '')}\n\n"
        f"{issue.get('body') or ''}"
    )
    task = task_from_text(store, text, source="webhook")
    logger.info(
        "task created from webhook",
        extra={"operation": "intake", "task": task.id, "issue": issue.get("number")},
    )
    return {"status": "created", "task_id": task.id}
