"""Parsing helpers for structured (JSON) agent responses."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^```[\w-]*\s*$", re.MULTILINE)


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from an agent response.

    Agents are instructed to reply with bare JSON, but models occasionally wrap
    it in markdown fences or prose. Raises ValueError when no object parses.
    """
    cleaned = _FENCE.sub("", text).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced {...} block.
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(cleaned)):
            char = cleaned[i]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start : i + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("{", start + 1)
    raise ValueError(f"no JSON object found in agent response ({len(text)} chars)")
