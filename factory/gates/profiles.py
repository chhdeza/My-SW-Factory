"""Language profile autodetection.

Python and Node are built in; other languages are declared in factory.yaml
under ``gates.custom`` with explicit lint/test commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factory.config import GatesConfig


@dataclass
class DetectedProfile:
    name: str
    lint: list[str] | None = None
    test: list[str] | None = None


BUILTIN_COMMANDS: dict[str, dict[str, list[str] | None]] = {
    "python": {
        "lint": ["ruff", "check", "."],
        "test": ["python", "-m", "pytest", "-q"],
    },
    "node": {
        "lint": ["npm", "run", "lint", "--if-present"],
        "test": ["npm", "test", "--silent", "--if-present"],
    },
}


def detect_profiles(target: str | Path, gates: GatesConfig) -> list[DetectedProfile]:
    """Return every profile whose marker files exist in the target directory."""
    target = Path(target)
    detected: list[DetectedProfile] = []
    all_profiles = {**gates.profiles, **gates.custom}
    for name, profile in all_profiles.items():
        if not any((target / marker).exists() for marker in profile.detect):
            continue
        builtin = BUILTIN_COMMANDS.get(name, {})
        detected.append(
            DetectedProfile(
                name=name,
                lint=profile.lint or builtin.get("lint"),
                test=profile.test or builtin.get("test"),
            )
        )
    return detected
