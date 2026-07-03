"""Per-run agent transcripts with secret redaction and retention.

Traces are JSON files under ``.factory/traces/YYYY-MM-DD/<trace_id>.json``.
Secrets are scrubbed BEFORE anything touches disk. Local only - no external
tracing service.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from factory.config import TracingConfig

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),                     # OpenAI/Anthropic style
    re.compile(r"\bcursor_[A-Za-z0-9_-]{10,}"),                 # Cursor API keys
    re.compile(r"\bkey_[A-Za-z0-9]{20,}"),                      # generic API keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),                # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                        # AWS access keys
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),              # Slack tokens
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),      # bearer headers
    re.compile(                                                  # KEY=value pairs
        r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*)"
        r"\s*[=:]\s*\S+"
    ),
]


def redact(text: str) -> str:
    for pattern in _PATTERNS[:-1]:
        text = pattern.sub(REDACTED, text)
    # Keep the variable name, redact only the value.
    text = _PATTERNS[-1].sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


class TraceWriter:
    def __init__(self, base_dir: str | Path, config: TracingConfig) -> None:
        self.base_dir = Path(base_dir)
        self.config = config

    def write(
        self,
        *,
        role: str,
        prompt: str,
        output: str,
        provider: str = "",
        model: str = "",
        status: str = "",
        events: list[dict[str, Any]] | None = None,
    ) -> str:
        """Write one redacted transcript; returns the trace id ('' if disabled)."""
        if not self.config.enabled:
            return ""
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        record: dict[str, Any] = {
            "trace_id": trace_id,
            "ts": now.isoformat(timespec="seconds"),
            "role": role,
            "provider": provider,
            "model": model,
            "status": status,
            "prompt": prompt,
            "output": output,
            "events": events or [],
        }
        if self.config.redact:
            record = _redact_value(record)
        day_dir = self.base_dir / now.date().isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{trace_id}.json"
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return trace_id

    def list_traces(self, limit: int = 50) -> list[dict[str, str]]:
        """Newest-first trace index (bounded)."""
        if not self.base_dir.exists():
            return []
        entries: list[dict[str, str]] = []
        for day_dir in sorted(self.base_dir.iterdir(), reverse=True):
            if not day_dir.is_dir():
                continue
            for path in sorted(day_dir.glob("trace-*.json"), reverse=True):
                entries.append({"trace_id": path.stem, "day": day_dir.name})
                if len(entries) >= limit:
                    return entries
        return entries

    def read(self, trace_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"trace-[0-9a-f]{12}", trace_id):
            return None  # reject anything that could traverse paths
        for day_dir in self.base_dir.iterdir() if self.base_dir.exists() else []:
            candidate = day_dir / f"{trace_id}.json"
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        return None

    def apply_retention(self) -> int:
        """Delete day-directories older than the retention window."""
        if not self.base_dir.exists():
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=self.config.retention_days)).date()
        removed = 0
        for day_dir in self.base_dir.iterdir():
            if not day_dir.is_dir():
                continue
            try:
                day = datetime.fromisoformat(day_dir.name).date()
            except ValueError:
                continue
            if day < cutoff:
                shutil.rmtree(day_dir, ignore_errors=True)
                removed += 1
        return removed
