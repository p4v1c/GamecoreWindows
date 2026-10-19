"""Deploy this release onto a box that predates the code/data split.

This is the test that decides whether the split may ship. The release goes out
over OTA and production boxes take it **unattended**: `update/linux.sh` rsyncs
new code into `GAMECORE_PATH` and excludes `emu/` and `config/` — which are
exactly the two directories this phase teaches the code to stop assuming are
there. If the new code looked for data anywhere other than where the rsync
leaves it, every box would come up with an empty library and no settings, and
nobody would be in front of it.

The rollback is worse. `update/linux.sh` restores from `${GAMECORE_PATH}.prev`,
which holds the *old* code. Had the update moved data, a rollback would face
the old code with a moved tree — two halves out of step and no procedure to
recombine them. So the property asserted here is the strong one: **the update
moves zero bytes of data, and the rollback returns a working box.**

Everything is synthetic. The tree in `tmp_path` imitates an installed box; the
release is `git archive HEAD`, which is what the OTA actually ships. Nothing
here can see, let alone touch, a real installation.

The excludes are **parsed out of `update/linux.sh`** rather than retyped. A
copy would be right the day it was written and wrong the day somebody adds an
exclude — and its being wrong would look exactly like this test passing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UPDATER = REPO / "update" / "linux.sh"

pytestmark = [pytest.mark.linux_until_port(8), pytest.mark.skipif(
    not shutil.which("rsync") or not shutil.which("git"),
    reason="the OTA path is rsync and the release is git archive")]


def _release_excludes() -> list[str]:
    """The `--exclude=` flags of the rsync that installs the new files.

    Anchored on the line that ends the invocation so this reads the deploy
    rsync and not the `.prev` snapshot's, which has a different list.
    """
    text = UPDATER.read_text(encoding="utf-8")
    block = text.split("rsync -a \\", 1)[1].split('"${SRC_DIR}/"', 1)[0]
    found = re.findall(r"--exclude='([^']+)'", block)
    assert found, "could not find the deploy rsync's excludes in update/linux.sh"
    return found


# What an installed box has that the release must not touch. Values are the
# marker contents; the test asserts they come through byte-identical.
_USER_DATA = {
    "emu/duckstation/Crash Bandicoot.bin": "a ROM the player dumped",
    "emu/duckstation/Crash Bandicoot.cue": 'FILE "Crash Bandicoot.bin" BINARY\n',
    "emu/covers/duckstation/Crash Bandicoot.png": "cover art",
    "emu/gamescrape/launchbox.sqlite": "the 234 MB index, in spirit",
    "assets/overlays/duckstation.png": "a bezel the player uploaded",
    "assets/logos/duckstation.png": "a logo the operator replaced",
    "config/theme.json": '{"active": "shelf"}',
    "config/standby.json": '{"enabled": true, "screensaver_mins": 10}',
}


def _installed_box(root: Path) -> None:
    """A box running a release from before the split."""
    for rel, content in _USER_DATA.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    (root / "config" / "systems.json").write_text(json.dumps([{
        "id": "duckstation", "label": "PS1", "platform": "PS1",
        "type": "emulator", "color": "#fff",
        "iconPath": "assets/logos/duckstation.png",
        "path": "/bin/true", "args": "",
        "romsPath": "emu/duckstation/",
        "extensions": ["*.cue"], "libretroSystems": [],
    }]))
    (root / "config" / "apps.json").write_text("[]")
    (root / "config" / "addons.json").write_text(json.dumps({
        "rom-manager": {"label": "ROM Manager", "version": "1.2",
                        "type": "web", "port": 8080, "path": "/roms"}}))

    # Old code, which the release must replace. A file that survives here is
    # the OTA failing to deploy, which this test would otherwise not notice.
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "config.py").write_text("# v1.0.6 backend\n")
    (root / "VERSION").write_text("v1.0.6")


def _release(tmp_path: Path) -> Path:
    """The tree the OTA would ship: the tracked files, as they stand now.

    Tracked, so `.venv/`, `node_modules/` and scratch files stay out exactly as
    they do from a release tarball. As they stand **now** rather than at HEAD,
    because this is the gate that decides whether the phase may merge — reading
    HEAD would test the code of the previous commit and go green on a defect
    sitting in the working tree, which is precisely when it is needed.
    """
    src = tmp_path / "release"
    src.mkdir()
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                             capture_output=True, check=True).stdout
    subprocess.run(["rsync", "-a", "--from0", "--files-from=-",
                    f"{REPO}/", f"{src}/"],
                   input=tracked, check=True, capture_output=True)
    return src


def _deploy(src: Path, dest: Path) -> None:
    """`update/linux.sh`'s deploy rsync, with its own excludes. No --delete —
    the script does not use one, and adding it here would test a script that
    does not exist."""
    subprocess.run(
        ["rsync", "-a", *[f"--exclude={e}" for e in _release_excludes()],
         f"{src}/", f"{dest}/"], check=True, capture_output=True)


_PROBE = r"""
import json, sys
root = sys.argv[1]
sys.path.insert(0, root)

from backend.services import paths
from backend.services import systems as sys_router
from backend.routers import addons as addons_router
from backend import utils

out = {}
out["data_root"] = str(paths.GAMECORE_DATA)
out["is_split"] = paths.is_split()

rows = sys_router.get_systems()
out["systems"] = [r["id"] for r in rows]

# The library, resolved the way routers/games.py resolves it.
system = rows[0]
roms = paths.resolve_data_path(system["romsPath"])
out["roms_dir"] = str(roms)
out["games"] = sorted(p.name for p in roms.glob("*.cue"))

# The containment check that gates every launch.
out["launch_allowed"] = bool(utils.rom_in_root(
    system, str(roms / "Crash Bandicoot.cue")))

out["covers"] = sorted(p.name for p in paths.covers_dir().glob("**/*.png"))
out["overlay"] = (paths.overlays_dir() / "duckstation.png").read_text()
out["theme"] = (paths.config_dir() / "theme.json").read_text()
out["addons"] = sorted(addons_router._registry())
out["index_present"] = (paths.media_index_dir() / "launchbox.sqlite").exists()
print(json.dumps(out))
"""


def _probe(root: Path) -> dict:
    """Start the backend the way systemd does — a process whose GAMECORE_PATH
    is the install — and ask it what it can see."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("GAMECORE_DATA", "GAMECORE_TEST_ROOT")}
    env["GAMECORE_PATH"] = str(root)
    env["HOME"] = str(root.parent / "home")
    Path(env["HOME"]).mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, "-c", _PROBE, str(root)],
                       capture_output=True, text=True, timeout=180, env=env)
    assert r.returncode == 0, f"the deployed backend could not start:\n{r.stderr}"
    return json.loads(r.stdout)


@pytest.fixture
def box(tmp_path):
    root = tmp_path / "opt" / "GameCore"
    root.mkdir(parents=True)
    _installed_box(root)
    return root


def test_the_release_deploys_onto_the_old_layout_and_the_box_still_works(box, tmp_path):
    """Launch, ROMs, settings, addons — after the rsync, on unmoved data."""
    _deploy(_release(tmp_path), box)
    seen = _probe(box)

    assert seen["is_split"] is False, (
        "an OTA'd box must resolve data to the install, since that is where "
        "the rsync left it")
    assert seen["data_root"] == str(box)
    assert seen["systems"] == ["duckstation"]
    assert seen["games"] == ["Crash Bandicoot.cue"], "the library came up empty"
    assert seen["launch_allowed"], "the containment check would refuse to launch"
    assert seen["covers"] == ["Crash Bandicoot.png"]
    assert seen["overlay"] == _USER_DATA["assets/overlays/duckstation.png"]
    assert seen["theme"] == _USER_DATA["config/theme.json"]
    assert seen["addons"] == ["rom-manager"]
    assert seen["index_present"], "the 234 MB index must not have to be rebuilt"


def test_the_release_moves_no_user_data(box, tmp_path):
    """Byte-for-byte, because 'the box still works' can be true while a file
    has quietly been rewritten — and the one that notices is the player whose
    save or bezel is gone."""
    _deploy(_release(tmp_path), box)
    for rel, content in _USER_DATA.items():
        assert (box / rel).read_text() == content, f"{rel} was touched"


def test_the_release_actually_replaced_the_code(box, tmp_path):
    """The other half. A deploy that changes nothing would pass every
    assertion above, so check the new code is genuinely there."""
    _deploy(_release(tmp_path), box)
    assert "v1.0.6 backend" not in (box / "backend" / "config.py").read_text()
    assert (box / "backend" / "services" / "paths.py").exists()


def test_the_excludes_stay_while_the_data_is_still_inside_the_install():
    """Two facts that must change together, checked together.

    The excludes protect `emu/` and `config/` because they sit inside the tree
    the rsync writes into. Removing one while the data is still there deletes
    the ROM library on the first update — the worst outcome in this repository,
    reached by a change that looks like tidying up.

    So the condition is not "this phase" or "not yet", which nobody can check.
    It is the default in `services/paths.py`: while GAMECORE_DATA falls back to
    the install, the data is inside it and the excludes are load-bearing. The
    day that default becomes a separate tree, this test stops demanding them by
    itself — no edit needed, and no way to forget.
    """
    from backend.services import paths

    if paths.is_split():
        pytest.skip("the suite is running with the roots already separate")

    excludes = _release_excludes()
    for needed in ("emu/", "config/", "assets/overlays/", "assets/logos/"):
        assert needed in excludes, (
            f"update/linux.sh no longer excludes {needed}, but the data still "
            "lives inside the install — the next OTA would delete it")


def test_rolling_back_returns_a_working_box(box, tmp_path):
    """`update/linux.sh` snapshots into `${GAMECORE_PATH}.prev` and restores
    with `rsync -a ${PREV_DIR}/ ${GAMECORE_PATH}/`.

    The snapshot excludes the data, so the restore puts the OLD code back over
    data that never moved. That is only safe because the update moved nothing —
    which is what this asserts, from the other direction.
    """
    prev = Path(str(box) + ".prev")
    subprocess.run(
        ["rsync", "-a", "--delete",
         "--exclude=.venv/", "--exclude=node_modules/", "--exclude=emu/",
         "--exclude=config/", "--exclude=VERSION",
         f"{box}/", f"{prev}/"], check=True, capture_output=True)

    _deploy(_release(tmp_path), box)
    # The restore command the script prints, verbatim — no --delete.
    subprocess.run(["rsync", "-a", f"{prev}/", f"{box}/"],
                   check=True, capture_output=True)

    assert (box / "backend" / "config.py").read_text() == "# v1.0.6 backend\n", (
        "the rollback did not restore the previous code")
    for rel, content in _USER_DATA.items():
        assert (box / rel).read_text() == content, (
            f"{rel} did not survive update-then-rollback")
    assert json.loads((box / "config" / "systems.json").read_text())[0]["id"] \
        == "duckstation"
