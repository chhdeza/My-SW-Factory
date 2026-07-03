"""Free/open-source gate toolchain installer.

The gates degrade gracefully (SKIP) when a tool is missing; this module
installs everything so as few checks skip as possible:

- Python tools (ruff, pytest, pytest-cov, bandit, pip-audit, semgrep) are
  pip-installed into the current environment.
- Binary tools (gitleaks, actionlint) are downloaded from their GitHub
  releases into ``~/.factory/bin``, which the sandbox prepends to PATH.

Everything here is best-effort: a failed install is reported, never fatal.
"""

from __future__ import annotations

import io
import json
import logging
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

BIN_DIR = Path.home() / ".factory" / "bin"
PIP_TIMEOUT_SECONDS = 600
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)

# x86_64 and arm64 spellings used across release asset names.
_MACHINE_ALIASES = {
    "amd64": ("x64", "amd64", "x86_64"),
    "x86_64": ("x64", "amd64", "x86_64"),
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("arm64", "aarch64"),
}


@dataclass(frozen=True)
class ToolSpec:
    name: str                  # executable looked up on PATH
    kind: str                  # "pip" | "binary"
    purpose: str
    pip_packages: tuple[str, ...] = ()
    github_repo: str = ""      # owner/repo for binary releases
    windows_ok: bool = True


CATALOG: tuple[ToolSpec, ...] = (
    ToolSpec("ruff", "pip", "quality gate: linting", pip_packages=("ruff",)),
    ToolSpec("pytest", "pip", "quality gate: tests + coverage",
             pip_packages=("pytest", "pytest-cov")),
    ToolSpec("bandit", "pip", "security gate: Python SAST", pip_packages=("bandit",)),
    ToolSpec("pip-audit", "pip", "security gate: dependency vulnerabilities",
             pip_packages=("pip-audit",)),
    ToolSpec("semgrep", "pip", "security gate: multi-language SAST",
             pip_packages=("semgrep",), windows_ok=False),
    ToolSpec("gitleaks", "binary", "security gate: secret detection",
             github_repo="gitleaks/gitleaks"),
    ToolSpec("actionlint", "binary", "quality gate: GitHub Actions workflow lint",
             github_repo="rhysd/actionlint"),
)


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def is_installed(spec: ToolSpec) -> bool:
    if shutil.which(spec.name) is not None:
        return True
    exe = f"{spec.name}.exe" if _is_windows() else spec.name
    # Also look next to the interpreter (venv Scripts/bin may not be on PATH
    # when the factory entry point is invoked directly) and in ~/.factory/bin.
    return (BIN_DIR / exe).exists() or (Path(sys.executable).parent / exe).exists()


def supported_here(spec: ToolSpec) -> bool:
    return spec.windows_ok or not _is_windows()


def tool_status() -> list[tuple[ToolSpec, str]]:
    """(spec, status) for every catalog tool: installed | missing | unsupported."""
    rows = []
    for spec in CATALOG:
        if not supported_here(spec):
            status = "unsupported on this OS (runs in CI)"
        elif is_installed(spec):
            status = "installed"
        else:
            status = "missing"
        rows.append((spec, status))
    return rows


# -- installers --------------------------------------------------------------


def _pip_install(packages: tuple[str, ...]) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", *packages],
        capture_output=True, text=True, timeout=PIP_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pip install {' '.join(packages)} failed: "
                           f"{proc.stderr[-500:]}")
    return f"pip installed {', '.join(packages)}"


def pick_asset(asset_names: list[str], system: str, machine: str) -> str | None:
    """Pick the release asset for this platform (pure, for testability)."""
    aliases = _MACHINE_ALIASES.get(machine.lower(), (machine.lower(),))
    for name in asset_names:
        lowered = name.lower()
        if not lowered.endswith((".zip", ".tar.gz", ".tgz")):
            continue
        if system in lowered and any(alias in lowered for alias in aliases):
            return name
    return None


def _extract_binary(payload: bytes, asset_name: str, tool_name: str) -> bytes:
    wanted = {tool_name, f"{tool_name}.exe"}
    if asset_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for member in archive.namelist():
                if Path(member).name in wanted:
                    return archive.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                if Path(member.name).name in wanted and member.isfile():
                    extracted = archive.extractfile(member)
                    if extracted is not None:
                        return extracted.read()
    raise RuntimeError(f"binary {tool_name!r} not found inside {asset_name}")


def _install_binary(spec: ToolSpec) -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        release = client.get(
            f"https://api.github.com/repos/{spec.github_repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        if release.status_code != 200:
            raise RuntimeError(f"could not query {spec.github_repo} releases: "
                               f"HTTP {release.status_code}")
        assets = {a["name"]: a["browser_download_url"]
                  for a in release.json().get("assets", [])}
        chosen = pick_asset(list(assets), system, machine)
        if chosen is None:
            raise RuntimeError(
                f"no {spec.name} release asset for {system}/{machine}"
            )
        payload = client.get(assets[chosen])
        if payload.status_code != 200:
            raise RuntimeError(f"download failed: HTTP {payload.status_code}")

    binary = _extract_binary(payload.content, chosen, spec.name)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    exe = f"{spec.name}.exe" if _is_windows() else spec.name
    target = BIN_DIR / exe
    target.write_bytes(binary)
    if not _is_windows():
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    version = release.json().get("tag_name", "latest")
    return f"downloaded {spec.name} {version} -> {target}"


def install_missing() -> list[str]:
    """Install every missing, platform-supported tool. Returns report lines."""
    report: list[str] = []
    for spec, status in tool_status():
        if status == "installed":
            report.append(f"[ok]   {spec.name}: already installed")
            continue
        if status.startswith("unsupported"):
            report.append(f"[skip] {spec.name}: {status}")
            continue
        try:
            if spec.kind == "pip":
                detail = _pip_install(spec.pip_packages)
            else:
                detail = _install_binary(spec)
            report.append(f"[ok]   {spec.name}: {detail}")
        except (RuntimeError, OSError, subprocess.TimeoutExpired,
                httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("tool install failed",
                           extra={"operation": "toolchain", "tool": spec.name,
                                  "error": str(exc)})
            report.append(f"[fail] {spec.name}: {exc}")
    return report
