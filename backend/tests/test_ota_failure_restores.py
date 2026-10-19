"""An OTA that fails after the new files landed puts the previous code back.

`update/linux.sh` rsyncs the release over the live install before pip and the
session-helper refresh, either of which can fail. It then exited with the new
code on disk, VERSION unwritten, and the next reboot ran new code on old
dependencies. The script runs here for real, in a synthetic box, with the
network, sudo and pip stubbed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.linux_until_port(8), pytest.mark.skipif(not shutil.which("rsync"),
                                reason="the OTA installs with rsync")]

_USER_DATA = {
    "emu/duckstation/game.bin": "a ROM",
    "config/systems.json": "[]",
    "assets/overlays/duckstation.png": "a bezel the player uploaded",
    "addons/rom-manager/state.json": "{}",
}


def _write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)


def _box(root: Path) -> None:
    _write(root / "backend" / "config.py", "# old backend\n")
    _write(root / "backend" / "requirements.txt", "fastapi\n")
    _write(root / "frontend" / "dist" / "index.html", "old ui")
    _write(root / "VERSION", "v1.0.0")
    _write(root / ".venv" / "bin" / "pip",
           '#!/bin/sh\nexit "${STUB_PIP_EXIT:-0}"\n', 0o755)
    for rel, content in _USER_DATA.items():
        _write(root / rel, content)


def _release(tmp: Path) -> Path:
    src = tmp / "release"
    # A different size: rsync skips a file of equal size and mtime.
    _write(src / "backend" / "config.py", "# the new backend\n")
    _write(src / "backend" / "added_by_release.py", "")
    _write(src / "backend" / "requirements.txt", "fastapi\nnewdep\n")
    _write(src / "frontend" / "dist" / "index.html", "the new ui")
    archive = tmp / "gamecore-ota.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for child in src.iterdir():
            tar.add(child, arcname=child.name)
    return archive


def _stubs(bin_dir: Path, archive: Path) -> None:
    release_json = ('{"tag_name": "v2.0.0", "assets": [{"name": "gamecore-ota.tar.gz",'
                    ' "browser_download_url": "https://example.invalid/ota.tar.gz"}]}')
    _write(bin_dir / "curl", f"""#!/bin/sh
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then cp '{archive}' "$2"; exit 0; fi
  shift
done
printf '%s' '{release_json}'
""", 0o755)
    _write(bin_dir / "sudo", '#!/bin/sh\nexit "${STUB_SUDO_EXIT:-1}"\n', 0o755)
    _write(bin_dir / "systemctl", "#!/bin/sh\nexit 1\n", 0o755)


def _run_update(tmp_path: Path, **env_overrides: str) -> tuple[Path, subprocess.CompletedProcess]:
    box = tmp_path / "opt" / "GameCore"
    _box(box)
    # A copy beside a stub preflight: the real one checks /etc and /usr/local.
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    shutil.copy(REPO / "update" / "linux.sh", update_dir / "linux.sh")
    _write(update_dir / "check-session-prerequisites.sh", "exit 0\n")
    bin_dir = tmp_path / "bin"
    _stubs(bin_dir, _release(tmp_path))
    (tmp_path / "tmp").mkdir()
    (tmp_path / "home").mkdir()
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "GAMECORE_PATH": str(box), "GAMECORE_DATA": str(box),
           "TMPDIR": str(tmp_path / "tmp"), "HOME": str(tmp_path / "home"),
           **env_overrides}
    result = subprocess.run(["bash", str(update_dir / "linux.sh")], env=env,
                            capture_output=True, text=True, timeout=120)
    return box, result


def _assert_previous_install(box: Path, result: subprocess.CompletedProcess) -> None:
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert (box / "backend" / "config.py").read_text() == "# old backend\n", out
    assert not (box / "backend" / "added_by_release.py").exists(), (
        "a file the release added survived the rollback")
    assert (box / "frontend" / "dist" / "index.html").read_text() == "old ui"
    assert (box / "VERSION").read_text() == "v1.0.0", "the update must be offered again"
    for rel, content in _USER_DATA.items():
        assert (box / rel).read_text() == content, f"{rel} was touched"
    assert "restored" in out.lower(), out


def test_a_failed_pip_install_restores_the_previous_code(tmp_path):
    box, result = _run_update(tmp_path, STUB_PIP_EXIT="1")
    assert "pip install failed" in result.stdout
    _assert_previous_install(box, result)


def test_a_failed_session_refresh_restores_the_previous_code(tmp_path):
    box, result = _run_update(tmp_path, STUB_PIP_EXIT="0", STUB_SUDO_EXIT="1")
    assert "session migration failed" in result.stdout
    _assert_previous_install(box, result)
