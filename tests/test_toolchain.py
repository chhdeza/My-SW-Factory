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
            "opengrep", "opengrep-rules", "gitleaks", "actionlint"} == names


def test_pick_asset_windows():
    assets = [
        "gitleaks_8.18.4_darwin_x64.tar.gz",
        "gitleaks_8.18.4_linux_x64.tar.gz",
        "gitleaks_8.18.4_windows_x64.zip",
        "gitleaks_8.18.4_windows_armv7.zip",
        "checksums.txt",
    ]
    assert pick_asset(assets, "windows", "AMD64",
                      "gitleaks") == "gitleaks_8.18.4_windows_x64.zip"


def test_pick_asset_linux_amd64_naming():
    assets = ["actionlint_1.7.7_linux_amd64.tar.gz", "actionlint_1.7.7_linux_arm64.tar.gz"]
    assert pick_asset(assets, "linux", "x86_64",
                      "actionlint") == "actionlint_1.7.7_linux_amd64.tar.gz"
    assert pick_asset(assets, "linux", "aarch64",
                      "actionlint") == "actionlint_1.7.7_linux_arm64.tar.gz"


def test_pick_asset_opengrep_bare_binaries():
    """Opengrep ships unarchived binaries, x86-named 64-bit builds, and
    opengrep-core_* companions that must never be picked."""
    assets = [
        "opengrep-core_windows_x86.zip",
        "opengrep_manylinux_x86",
        "opengrep_manylinux_x86.sig",
        "opengrep_osx_arm64",
        "opengrep_windows_x86.exe",
        "opengrep_windows_x86.exe.cert",
    ]
    assert pick_asset(assets, "windows", "AMD64",
                      "opengrep") == "opengrep_windows_x86.exe"
    assert pick_asset(assets, "linux", "x86_64",
                      "opengrep") == "opengrep_manylinux_x86"
    assert pick_asset(assets, "darwin", "arm64", "opengrep") == "opengrep_osx_arm64"


def test_pick_asset_none_for_unknown_platform():
    assert pick_asset(["tool_windows_x64.zip"], "plan9", "mips", "tool") is None


def test_semgrep_unsupported_on_windows(monkeypatch):
    monkeypatch.setattr(toolchain.platform, "system", lambda: "Windows")
    spec = next(s for s in CATALOG if s.name == "semgrep")
    assert not supported_here(spec)
    monkeypatch.setattr(toolchain.platform, "system", lambda: "Linux")
    assert supported_here(spec)


def test_tool_status_marks_installed(monkeypatch):
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(toolchain, "is_installed", lambda spec: True)
    assert all(
        status == "installed"
        for spec, status in tool_status()
        if supported_here(spec) and toolchain.needed_here(spec)
    )


def test_opengrep_only_needed_where_semgrep_is_not(monkeypatch):
    opengrep = next(s for s in CATALOG if s.name == "opengrep")
    monkeypatch.setattr(toolchain.platform, "system", lambda: "Windows")
    assert toolchain.needed_here(opengrep)      # semgrep unsupported here
    monkeypatch.setattr(toolchain.platform, "system", lambda: "Linux")
    assert not toolchain.needed_here(opengrep)  # semgrep covers it


def test_status_explains_fallback_on_windows(monkeypatch):
    monkeypatch.setattr(toolchain.platform, "system", lambda: "Windows")
    statuses = dict((spec.name, status) for spec, status in tool_status())
    assert statuses["semgrep"] == "unsupported on this OS (opengrep is used instead)"


def test_install_skips_unneeded_fallback(monkeypatch):
    monkeypatch.setattr(toolchain.platform, "system", lambda: "Linux")
    monkeypatch.setattr(toolchain, "is_installed", lambda spec: spec.name != "opengrep")

    def boom(*args, **kwargs):
        raise AssertionError("opengrep must not install where semgrep is supported")

    monkeypatch.setattr(toolchain, "_install_binary", boom)
    report = install_missing()
    opengrep_line = next(line for line in report if "opengrep:" in line)
    assert opengrep_line.startswith("[skip]")


def test_install_missing_skips_installed_without_side_effects(monkeypatch):
    monkeypatch.setattr(toolchain, "is_installed", lambda spec: True)

    def boom(*args, **kwargs):
        raise AssertionError("must not attempt installs when everything is present")

    monkeypatch.setattr(toolchain, "_pip_install", boom)
    monkeypatch.setattr(toolchain, "_install_binary", boom)
    monkeypatch.setattr(toolchain, "_git_clone", boom)

    report = install_missing()

    assert all("[ok]" in line or "[skip]" in line for line in report)


def test_install_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(toolchain, "is_installed", lambda spec: False)
    monkeypatch.setattr(toolchain, "supported_here", lambda spec: True)
    monkeypatch.setattr(toolchain, "needed_here", lambda spec: True)
    for installer in ("_pip_install", "_install_binary", "_git_clone"):
        monkeypatch.setattr(
            toolchain, installer,
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broke")),
        )

    report = install_missing()

    assert len(report) == len(CATALOG)
    assert all(line.startswith("[fail]") for line in report)


def test_extract_binary_bare_executable_passthrough():
    payload = b"MZBINARY"
    assert toolchain._extract_binary(payload, "opengrep_windows_x86.exe",
                                     "opengrep") == payload


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
