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

# x86_64 and arm64 spellings used across release asset names, in preference
# order ("x86" last: opengrep names its 64-bit builds *_x86).
_MACHINE_ALIASES = {
    "amd64": ("x64", "amd64", "x86_64", "x86"),
    "x86_64": ("x64", "amd64", "x86_64", "x86"),
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("arm64", "aarch64"),
}

_SYSTEM_ALIASES = {
    "windows": ("windows",),
    "linux": ("linux",),           # also matches "manylinux"
    "darwin": ("darwin", "osx", "macos"),
}

_NON_BINARY_SUFFIXES = (".sig", ".cert", ".pem", ".txt", ".json", ".sbom", ".md5",
                        ".sha256", ".sha512")


@dataclass(frozen=True)
class ToolSpec:
    name: str                  # executable looked up on PATH (or dir for kind=git)
    kind: str                  # "pip" | "binary" | "git"
    purpose: str
    pip_packages: tuple[str, ...] = ()
    github_repo: str = ""      # owner/repo for binary releases or git clone
    windows_ok: bool = True
    fallback_for: str = ""     # only install when the named tool is unsupported here


CATALOG: tuple[ToolSpec, ...] = (
    ToolSpec("ruff", "pip", "quality gate: linting", pip_packages=("ruff",)),
    ToolSpec("pytest", "pip", "quality gate: tests + coverage",
             pip_packages=("pytest", "pytest-cov")),
    ToolSpec("bandit", "pip", "security gate: Python SAST", pip_packages=("bandit",)),
    ToolSpec("pip-audit", "pip", "security gate: dependency vulnerabilities",
             pip_packages=("pip-audit",)),
    ToolSpec("semgrep", "pip", "security gate: multi-language SAST",
             pip_packages=("semgrep",), windows_ok=False),
    ToolSpec("opengrep", "binary", "security gate: SAST (semgrep fork, native Windows)",
             github_repo="opengrep/opengrep", fallback_for="semgrep"),
    ToolSpec("opengrep-rules", "git", "community SAST ruleset for opengrep",
             github_repo="opengrep/opengrep-rules", fallback_for="semgrep"),
    ToolSpec("gitleaks", "binary", "security gate: secret detection",
             github_repo="gitleaks/gitleaks"),
    ToolSpec("actionlint", "binary", "quality gate: GitHub Actions workflow lint",
             github_repo="rhysd/actionlint"),
)


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _spec_by_name(name: str) -> ToolSpec | None:
    return next((spec for spec in CATALOG if spec.name == name), None)


def is_installed(spec: ToolSpec) -> bool:
    if spec.kind == "git":
        return (Path.home() / ".factory" / spec.name).is_dir()
    if shutil.which(spec.name) is not None:
        return True
    exe = f"{spec.name}.exe" if _is_windows() else spec.name
    # Also look next to the interpreter (venv Scripts/bin may not be on PATH
    # when the factory entry point is invoked directly) and in ~/.factory/bin.
    return (BIN_DIR / exe).exists() or (Path(sys.executable).parent / exe).exists()


def supported_here(spec: ToolSpec) -> bool:
    return spec.windows_ok or not _is_windows()


def needed_here(spec: ToolSpec) -> bool:
    """Fallback tools are only needed where their primary is unsupported."""
    if not spec.fallback_for:
        return True
    primary = _spec_by_name(spec.fallback_for)
    return primary is None or not supported_here(primary)


def tool_status() -> list[tuple[ToolSpec, str]]:
    """(spec, status) for every catalog tool."""
    rows = []
    for spec in CATALOG:
        if not supported_here(spec):
            fallback = next(
                (s.name for s in CATALOG if s.fallback_for == spec.name), ""
            )
            status = (f"unsupported on this OS ({fallback} is used instead)"
                      if fallback else "unsupported on this OS (runs in CI)")
        elif not needed_here(spec):
            status = f"not needed here ({spec.fallback_for} is available)"
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


def pick_asset(
    asset_names: list[str], system: str, machine: str, tool_name: str
) -> str | None:
    """Pick the release asset for this platform (pure, for testability).

    Accepts archives (.zip/.tar.gz) and bare executables (opengrep ships
    unarchived binaries). Only assets named ``<tool>_...`` are considered so
    companion artifacts (e.g. opengrep-core_*) are never picked.
    """
    system_aliases = _SYSTEM_ALIASES.get(system.lower(), (system.lower(),))
    machine_aliases = _MACHINE_ALIASES.get(machine.lower(), (machine.lower(),))
    candidates = [
        name for name in asset_names
        if name.lower().startswith(f"{tool_name.lower()}_")
        and not name.lower().endswith(_NON_BINARY_SUFFIXES)
        and "checksum" not in name.lower()
    ]
    for march in machine_aliases:  # preference order matters (x64 before x86)
        for name in candidates:
            lowered = name.lower()
            if any(s in lowered for s in system_aliases) and march in lowered:
                return name
    return None


def _extract_binary(payload: bytes, asset_name: str, tool_name: str) -> bytes:
    wanted = {tool_name, f"{tool_name}.exe"}
    if not asset_name.endswith((".zip", ".tar.gz", ".tgz")):
        return payload  # bare executable asset (e.g. opengrep_windows_x86.exe)
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
        chosen = pick_asset(list(assets), system, machine, spec.name)
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


def _git_clone(spec: ToolSpec) -> str:
    dest = Path.home() / ".factory" / spec.name
    proc = subprocess.run(
        ["git", "clone", "--depth", "1",
         f"https://github.com/{spec.github_repo}.git", str(dest)],
        capture_output=True, text=True, timeout=PIP_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git clone {spec.github_repo} failed: {proc.stderr[-500:]}")
    return f"cloned {spec.github_repo} -> {dest}"


def install_missing() -> list[str]:
    """Install every missing, platform-supported, needed tool. Returns report lines."""
    report: list[str] = []
    installers = {"pip": lambda s: _pip_install(s.pip_packages),
                  "binary": _install_binary, "git": _git_clone}
    for spec, status in tool_status():
        if status == "installed":
            report.append(f"[ok]   {spec.name}: already installed")
            continue
        if status != "missing":
            report.append(f"[skip] {spec.name}: {status}")
            continue
        try:
            detail = installers[spec.kind](spec)
            report.append(f"[ok]   {spec.name}: {detail}")
        except (RuntimeError, OSError, subprocess.TimeoutExpired,
                httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("tool install failed",
                           extra={"operation": "toolchain", "tool": spec.name,
                                  "error": str(exc)})
            report.append(f"[fail] {spec.name}: {exc}")
    return report
