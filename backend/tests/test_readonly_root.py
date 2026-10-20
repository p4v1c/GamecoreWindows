"""The installation, actually mounted read-only, with the box still working.

Every other test in this phase checks that a path *resolves* somewhere
sensible. This one removes write permission from the whole code tree and then
asks the backend to do the things a player does — change a setting, have a
ROM appear, install and remove an addon.

It is the only check that cannot be satisfied by a path that merely looks
right. A module that still writes into the installation passes every resolution
test ever written and fails here, with an `EACCES` naming the file.

`chmod -R a-w` rather than a real read-only mount, because mounting needs root
and this has to run in CI. The distinction that matters — the kernel refusing
the write — is the same.

Skipped when running as root, which ignores the permission bits and would make
the whole file green while proving nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.linux_until_port(7), pytest.mark.skipif(
    (hasattr(os, "geteuid") and os.geteuid() == 0) or not shutil.which("rsync"),
    reason="root ignores the permission bits this test depends on")]


def _code_tree(dest: Path) -> None:
    """The tracked files — what an installation contains."""
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                             capture_output=True, check=True).stdout
    subprocess.run(["rsync", "-a", "--from0", "--files-from=-",
                    f"{REPO}/", f"{dest}/"],
                   input=tracked, check=True, capture_output=True)


def _data_tree(dest: Path) -> None:
    for sub in ("config", "emu/duckstation", "assets/overlays", "addons"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    (dest / "config" / "systems.json").write_text(json.dumps([{
        "id": "duckstation", "label": "PS1", "platform": "PS1",
        "type": "emulator", "color": "#fff", "iconPath": "x.png",
        "path": "/bin/true", "args": "", "romsPath": "emu/duckstation/",
        "extensions": ["*.cue"], "libretroSystems": [],
    }]))
    (dest / "config" / "apps.json").write_text("[]")


def _set_writable(root: Path, writable: bool) -> None:
    add = stat.S_IWUSR
    for p in [root, *root.rglob("*")]:
        try:
            mode = p.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            p.chmod((mode | add) if writable else (mode & ~0o222))
        except OSError:
            pass


# Runs inside the frozen tree. Each step is something a player does from the
# TV, and each one writes — which is the point.
_PROBE = r"""
import json, sys, io
code, data = sys.argv[1], sys.argv[2]
sys.path.insert(0, code)

from backend.services import paths, standby, themes
from backend.services import systems as sys_router

done = {}

# A ROM arrives (this is what the rom-manager addon does) and the library sees it.
rom = paths.roms_root() / "duckstation" / "Crash Bandicoot.cue"
rom.write_text('FILE "Crash Bandicoot.bin" BINARY\n')
rows = sys_router.get_systems()
roms_dir = paths.resolve_data_path(rows[0]["romsPath"])
done["library"] = sorted(p.name for p in roms_dir.glob("*.cue"))

# A setting changes.
standby.save_config({"enabled": False, "screensaver_mins": 3, "sleep_mins": 9})
done["standby"] = json.loads(standby.CONFIG_FILE.read_text())["screensaver_mins"]

# The player picks a theme.
themes.set_active(None)
done["theme_state_written"] = themes.STATE_FILE.exists()

done["code_root"] = code
done["data_root"] = str(paths.GAMECORE_DATA)
done["is_split"] = paths.is_split()
print(json.dumps(done))
"""


@pytest.fixture
def frozen(tmp_path):
    code, data = tmp_path / "opt" / "gamecore", tmp_path / "userdata"
    code.mkdir(parents=True)
    data.mkdir(parents=True)
    _code_tree(code)
    _data_tree(data)
    _set_writable(code, False)
    try:
        yield code, data
    finally:
        # Without this, pytest cannot delete tmp_path and every later test in
        # the session inherits the mess.
        _set_writable(code, True)


def _run(code: Path, data: Path, script: str, *args: str):
    env = {k: v for k, v in os.environ.items() if k != "GAMECORE_TEST_ROOT"}
    env["GAMECORE_PATH"] = str(code)
    env["GAMECORE_DATA"] = str(data)
    env["HOME"] = str(data.parent / "home")
    Path(env["HOME"]).mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-c", script, str(code), str(data), *args],
                          capture_output=True, text=True, timeout=180, env=env)


def test_the_tree_really_is_unwritable(frozen):
    """Guard the guard. If the chmod silently did nothing — a filesystem
    without permissions, a stray umask, root — every assertion below would
    pass while testing nothing at all."""
    code, _ = frozen
    with pytest.raises(PermissionError):
        (code / "backend" / "should-not-be-writable").write_text("x")


def test_the_box_works_with_the_code_frozen(frozen):
    code, data = frozen
    r = _run(code, data, _PROBE)
    assert r.returncode == 0, (
        "the backend could not work against a read-only installation:\n"
        + r.stderr)
    seen = json.loads(r.stdout)
    assert seen["is_split"] is True
    assert seen["data_root"] == str(data)
    assert seen["library"] == ["Crash Bandicoot.cue"]
    assert seen["standby"] == 3
    assert seen["theme_state_written"]


def test_nothing_was_written_into_the_installation(frozen):
    """A write that the kernel refused could still have been swallowed by a
    `try/except OSError` somewhere and reported as success. Compare the tree
    against itself instead of trusting the return code."""
    code, data = frozen
    before = {p.relative_to(code) for p in code.rglob("*")}
    _run(code, data, _PROBE)
    after = {p.relative_to(code) for p in code.rglob("*")}
    assert after == before, f"the installation gained: {sorted(after - before)}"


_ADDON_PROBE = r"""
import json, subprocess, sys, os
code, data = sys.argv[1], sys.argv[2]
repo = os.path.join(data, "addons", "_repo", "addons", "demo")
os.makedirs(repo, exist_ok=True)
json.dump({"name": "demo", "version": "1.0", "api": 1, "type": "web", "port": 9100},
          open(os.path.join(repo, "addon.json"), "w"))
for s in ("install.sh", "uninstall.sh"):
    open(os.path.join(repo, s), "w").write(
        '#!/usr/bin/env bash\ntouch "$ADDON_DATA_DIR/ran-$(basename $0)"\n')

env = {**os.environ, "GCA_REPO_DIR": os.path.join(data, "addons", "_repo"),
       "OFFLINE": "1", "PATH": "/usr/bin:/bin"}
cli = os.path.join(code, "install", "bin", "gamecore-addon")
out = {}
for action in ("install", "remove"):
    r = subprocess.run(["bash", cli, action, "demo"], env=env,
                       capture_output=True, text=True, timeout=120)
    out[action] = {"rc": r.returncode, "err": r.stderr[-400:]}
reg = os.path.join(data, "config", "addons.json")
out["registry_after_remove"] = json.load(open(reg)) if os.path.exists(reg) else None
print(json.dumps(out))
"""


def test_an_addon_installs_and_uninstalls_on_a_frozen_root(frozen):
    """The criterion addons were the hard case for: the CLI, the checkout and
    the registry all had to move to the data side for this to be possible at
    all."""
    code, data = frozen
    r = _run(code, data, _ADDON_PROBE)
    assert r.returncode == 0, r.stderr
    seen = json.loads(r.stdout)
    assert seen["install"]["rc"] == 0, seen["install"]["err"]
    assert seen["remove"]["rc"] == 0, seen["remove"]["err"]
    assert (data / "addons" / "demo" / "ran-install.sh").exists()
    assert seen["registry_after_remove"] == {}


def test_a_tarball_of_the_data_tree_restores_the_box(frozen, tmp_path):
    """`tar czf backup.tgz /userdata` is the promise the split is for. Checked
    by actually doing it: archive, delete the tree, restore, and ask the
    backend what it can see."""
    code, data = frozen
    _run(code, data, _PROBE)

    backup = tmp_path / "backup.tgz"
    subprocess.run(["tar", "czf", str(backup), "-C", str(data.parent), data.name],
                   check=True, capture_output=True)
    shutil.rmtree(data)
    subprocess.run(["tar", "xzf", str(backup), "-C", str(data.parent)],
                   check=True, capture_output=True)

    check = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from backend.services import paths
from backend.services import systems as sys_router
rows = sys_router.get_systems()
roms = paths.resolve_data_path(rows[0]["romsPath"])
print(json.dumps({
    "systems": [r["id"] for r in rows],
    "games": sorted(p.name for p in roms.glob("*.cue")),
    "standby": (paths.config_dir() / "standby.json").exists(),
}))
"""
    r = _run(code, data, check)
    assert r.returncode == 0, r.stderr
    seen = json.loads(r.stdout)
    assert seen["systems"] == ["duckstation"]
    assert seen["games"] == ["Crash Bandicoot.cue"]
    assert seen["standby"], "the settings did not survive the restore"
