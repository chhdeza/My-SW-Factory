"""Toolchain installer tests (no network, no real installs)."""

import factory.toolchain as toolchain
from factory.toolchain import (
    CATALOG,
    ToolSpec,
    install_missing,
    pick_asset,
    supported_here,
    tool_status,
)


def test_catalog_covers_all_gate_tools():
    names = {spec.name for spec in CATALOG}
    assert {"ruff", "pytest", "bandit", "pip-audit", "semgrep",
            "gitleaks", "actionlint"} == names


def test_pick_asset_windows():
    assets = [
        "gitleaks_8.18.4_darwin_x64.tar.gz",
        "gitleaks_8.18.4_linux_x64.tar.gz",
        "gitleaks_8.18.4_windows_x64.zip",
        "gitleaks_8.18.4_windows_armv7.zip",
        "checksums.txt",
    ]
    assert pick_asset(assets, "windows", "AMD64") == "gitleaks_8.18.4_windows_x64.zip"


def test_pick_asset_linux_amd64_naming():
    assets = ["actionlint_1.7.7_linux_amd64.tar.gz", "actionlint_1.7.7_linux_arm64.tar.gz"]
    assert pick_asset(assets, "linux", "x86_64") == "actionlint_1.7.7_linux_amd64.tar.gz"
    assert pick_asset(assets, "linux", "aarch64") == "actionlint_1.7.7_linux_arm64.tar.gz"


def test_pick_asset_none_for_unknown_platform():
    assert pick_asset(["tool_windows_x64.zip"], "plan9", "mips") is None


def test_semgrep_unsupported_on_windows(monkeypatch):
    monkeypatch.setattr(toolchain.platform, "system", lambda: "Windows")
    spec = next(s for s in CATALOG if s.name == "semgrep")
    assert not supported_here(spec)
    monkeypatch.setattr(toolchain.platform, "system", lambda: "Linux")
    assert supported_here(spec)


def test_tool_status_marks_installed(monkeypatch):
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert all(
        status == "installed"
        for spec, status in tool_status()
        if supported_here(spec)
    )


def test_install_missing_skips_installed_without_side_effects(monkeypatch):
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: f"/usr/bin/{name}")

    def boom(*args, **kwargs):
        raise AssertionError("must not attempt installs when everything is present")

    monkeypatch.setattr(toolchain, "_pip_install", boom)
    monkeypatch.setattr(toolchain, "_install_binary", boom)

    report = install_missing()

    assert all("[ok]" in line or "[skip]" in line for line in report)


def test_install_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(toolchain, "is_installed", lambda spec: False)
    monkeypatch.setattr(toolchain, "supported_here", lambda spec: True)
    monkeypatch.setattr(
        toolchain, "_pip_install",
        lambda pkgs: (_ for _ in ()).throw(RuntimeError("pip broke")),
    )
    monkeypatch.setattr(
        toolchain, "_install_binary",
        lambda spec: (_ for _ in ()).throw(RuntimeError("download broke")),
    )

    report = install_missing()

    assert len(report) == len(CATALOG)
    assert all(line.startswith("[fail]") for line in report)


def test_extract_binary_from_zip():
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("README.md", "docs")
        archive.writestr("gitleaks.exe", b"BINARY")
    payload = toolchain._extract_binary(buffer.getvalue(), "g.zip", "gitleaks")
    assert payload == b"BINARY"


def test_bin_dir_spec():
    spec = ToolSpec("x", "binary", "test", github_repo="a/b")
    assert spec.github_repo == "a/b"
    assert toolchain.BIN_DIR.name == "bin"
