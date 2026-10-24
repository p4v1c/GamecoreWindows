"""scripts/package-release.py and release.yml: what a release carries, and who may publish it.

The archive is checked against the repository's real tracked files with the
two build outputs faked, so a directory the installer or the updater calls
that is left out fails here, not on a box.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "update"))

import windows  # noqa: E402

_spec = importlib.util.spec_from_file_location("package_release", REPO / "scripts" / "package-release.py")
package = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(package)

WORKFLOW = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def _tracked() -> list[str]:
    try:
        return package.tracked_files(REPO)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")


def _archive_names(tmp_path: Path) -> set[str]:
    """Build a real archive from a copy of the tracked shipped files plus fake build outputs."""
    source = tmp_path / "tree"
    tracked = _tracked()
    for rel in tracked:
        if rel.split("/", 1)[0] in package.SHIPPED_DIRS or rel in package.SHIPPED_FILES:
            src = REPO / rel
            if src.is_file():
                dst = source / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
    for rel in ("frontend/dist/index.html", "electron/dist/win-unpacked/GameCore.exe"):
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / rel).write_bytes(b"built")
    archive = package.write_archive(package.collect(source, tracked), "v0.3.0", tmp_path / "out")
    with zipfile.ZipFile(archive) as z:
        return set(z.namelist())


@pytest.fixture(scope="module")
def names(tmp_path_factory):
    return _archive_names(tmp_path_factory.mktemp("release"))


def test_the_archive_holds_everything_stage_one_requires(names):
    missing = [f for f in windows.REQUIRED_FILES if f not in names]
    assert missing == []


def test_every_code_root_path_the_installer_calls_is_shipped(names):
    """The Linux workflow once left scripts/ out: the installers call it, and
    the break showed only on a real box. Paths joined onto the code root by an
    install step must arrive with the archive, unless the installer puts them
    there itself (Python, the venv, Caddy) or they come from the build (shell)."""
    called = set()
    for step in (REPO / "install" / "steps").glob("*.ps1"):
        for rel in re.findall(r"Join-Path \$(?:Settings\.)?CodeRoot '([^']+)'", step.read_text(encoding="utf-8")):
            called.add(rel.replace("\\", "/"))
    installed = {"python", ".venv", "caddy"}
    relevant = {p for p in called if p.split("/", 1)[0] not in installed}
    assert relevant, "the pattern no longer finds the installer's paths"
    missing = sorted(p for p in relevant if p not in names and not any(n.startswith(p + "/") for n in names))
    assert missing == []


def test_what_the_installer_adds_and_the_player_owns_never_ships(names):
    tops = {n.split("/", 1)[0] for n in names}
    assert tops.isdisjoint({"python", ".venv", "caddy", "emu", "logs", ".git", "docs"})
    assert not any("node_modules" in n or "__pycache__" in n or n.endswith(".pyc") for n in names)
    assert "shell/GameCore.exe" in names and "frontend/dist/index.html" in names
    assert not any(n.startswith("electron/dist/") for n in names)


def test_the_frontend_sources_ship_with_the_bundle(names):
    """A box with sources older than its bundle rolls the UI back on any rebuild."""
    assert any(n.startswith("frontend/src/") for n in names)


def test_version_is_the_tag_and_the_checksum_matches(tmp_path):
    source = tmp_path / "s"
    (source / "frontend/dist").mkdir(parents=True)
    (source / "frontend/dist/index.html").write_text("x")
    (source / "electron/dist/win-unpacked").mkdir(parents=True)
    (source / "electron/dist/win-unpacked/GameCore.exe").write_text("x")
    (source / "VERSION").write_text("v0.0.0\n")
    archive = package.write_archive(package.collect(source, ["VERSION"]), "v0.4.0", tmp_path / "out")
    with zipfile.ZipFile(archive) as z:
        assert z.read("VERSION").decode().strip() == "v0.4.0"
    line = (tmp_path / "out" / "gamecore-ota.zip.sha256").read_text()
    assert windows.expected_sha256(line, "") == windows.sha256_file(archive)
    assert line.split()[1] == windows.ASSET


def test_a_tree_without_the_build_outputs_is_refused(tmp_path):
    with pytest.raises(package.PackageError, match="frontend/dist is missing"):
        package.collect(tmp_path, [])


# ── release.yml: who may publish ────────────────────────────────────────────

def test_releases_come_from_pushes_to_main_only():
    """A dispatch from a branch, or a hand-pushed tag, published on Linux."""
    on = WORKFLOW.split("\non:", 1)[1].split("\nconcurrency:", 1)[0]
    assert "workflow_dispatch" not in on and "tags:" not in on and "pull_request" not in on
    assert "branches: [main]" in on
    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" in WORKFLOW


def test_two_merges_in_a_row_queue_instead_of_colliding():
    assert "concurrency:\n  group: release\n  cancel-in-progress: false" in WORKFLOW


def test_nothing_is_published_before_the_checks_pass():
    assert "uses: ./.github/workflows/ci.yml" in WORKFLOW and "needs: checks" in WORKFLOW
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "workflow_call:" in ci
    assert "continue-on-error" not in WORKFLOW


def test_the_published_assets_are_the_ones_the_updater_reads():
    assert f"release/{windows.ASSET} release/{windows.CHECKSUM_ASSET}" in WORKFLOW
    from backend.config import GITHUB_REPO, UPDATE_ASSET
    assert (GITHUB_REPO, UPDATE_ASSET) == (windows.REPO, windows.ASSET) == ("p4v1c/GamecoreWindows", "gamecore-ota.zip")
