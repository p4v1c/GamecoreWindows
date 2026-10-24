"""The catalogue merge the OTA runs, with the code and the data in two places.

After the swap, `update/apply-update.ps1` runs `update/windows.py finish`,
which merges the shipped catalogue into the box's `systems.json`. The Linux
updater once handed that step ONE directory for both the catalogue it reads
and the grid it writes: correct while the data lived inside the install, and
silently wrong once it did not. Every update merged into the abandoned copy
and the grid the box reads stopped receiving catalogue changes, with no error.

So it runs here as the updater runs it, with two roots that genuinely differ.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services.platform import filesystem

REPO = Path(__file__).resolve().parents[2]
STAGE_TWO = REPO / "update" / "apply-update.ps1"
STAGE_ONE = REPO / "update" / "windows.py"


@pytest.fixture
def two_roots(tmp_path):
    """A code root and a data root that are NOT the same directory.

    The code root is a thin mirror of the checkout — the catalogue and the
    backend package, symlinked, because `load_catalog` and `merge_file` are
    imported from it — with its own `config/systems.json` that must come out
    of this untouched. The data root holds the grid the box really reads.
    """
    code = tmp_path / "GameCore"
    data = tmp_path / "userdata"
    code.mkdir()
    filesystem.link_dir(code / "backend", REPO / "backend")
    filesystem.link_dir(code / "catalog", REPO / "catalog")
    (code / "config").mkdir()
    (data / "config").mkdir(parents=True)

    stale = [{"id": "STALE-DO-NOT-TOUCH", "type": "emulator", "label": "x",
              "platform": "x", "color": "#000", "path": "x", "args": "",
              "romsPath": "emu/x/", "extensions": [], "libretroSystems": []}]
    (code / "config" / "systems.json").write_text(json.dumps(stale))
    # The live grid: mgba as an old box has it, without `consoles`.
    live = [{"id": "mgba", "type": "emulator", "label": "Game Boy Advance",
             "platform": "GBA", "color": "#96c800", "path": "flatpak",
             "args": "run io.mgba.mGBA --fullscreen", "romsPath": "emu/mgba/",
             "extensions": ["*.gba", "*.gbc", "*.gb", "*.zip"],
             "libretroSystems": []}]
    (data / "config" / "systems.json").write_text(json.dumps(live))
    return code, data


def _run_merge(code: Path, data: Path) -> subprocess.CompletedProcess:
    """`windows.py finish`, as stage 2 runs it; the code root has no themes, so only the merge acts."""
    env = {**os.environ, "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, str(STAGE_ONE), "finish", "--root", str(code), "--data", str(data)],
                          text=True, capture_output=True, timeout=120, env=env)


def test_the_updater_hands_the_merge_both_roots():
    """The shape of the call, before its effect: if only one root is passed,
    the tests below can pass by accident on the day both point at one place."""
    text = STAGE_TWO.read_text(encoding="utf-8")
    call = next(line for line in text.splitlines() if "'finish'" in line)
    assert "'--root', $UpdatePlan.code_root" in call and "'--data', $UpdatePlan.data_root" in call, call


def test_the_merge_writes_the_grid_the_box_reads_not_the_install_copy(two_roots):
    code, data = two_roots
    r = _run_merge(code, data)
    assert r.returncode == 0, r.stderr

    live = json.loads((data / "config" / "systems.json").read_text())
    mgba = next(s for s in live if s["id"] == "mgba")
    assert [c["id"] for c in mgba["consoles"]] == ["gba", "gbc", "gb"], \
        "the live grid did not receive the merge"

    # And the copy under the install is exactly what it was — not merged into,
    # not backed up beside, not touched.
    assert json.loads((code / "config" / "systems.json").read_text()) == [
        {"id": "STALE-DO-NOT-TOUCH", "type": "emulator", "label": "x",
         "platform": "x", "color": "#000", "path": "x", "args": "",
         "romsPath": "emu/x/", "extensions": [], "libretroSystems": []}]
    assert not (code / "config" / "systems.json.bak-merge").exists()
    assert "consoles filled in" in r.stdout


def test_the_operator_s_removed_list_is_read_from_the_data_root(two_roots):
    """`catalog-removed.json` is the operator saying "not this one". It lives
    with the grid. Read from the install root it would be a stale copy that
    lists nothing, and every declined system would come back on each update."""
    code, data = two_roots
    (data / "config" / "catalog-removed.json").write_text(json.dumps(["cemu", "xenia"]))
    (code / "bin").mkdir()
    (code / "bin" / "duckstation.AppImage").write_bytes(b"\x7fELF")
    r = _run_merge(code, data)
    assert r.returncode == 0, r.stderr
    ids = {s["id"] for s in json.loads((data / "config" / "systems.json").read_text())}
    assert "cemu" not in ids and "xenia" not in ids, r.stdout
    assert "duckstation" in ids                    # a genuinely new one still lands


def test_the_operator_s_own_packs_are_read_from_the_data_root(two_roots):
    """`config/catalog.d/` is the operator's tier of the catalogue and it
    outranks the shipped one. It moves with the data."""
    code, data = two_roots
    local = data / "config" / "catalog.d" / "mgba"
    local.mkdir(parents=True)
    shutil.copy2(REPO / "catalog" / "mgba" / "pack.json", local / "pack.json")
    shutil.copy2(REPO / "catalog" / "mgba" / "logo.png", local / "logo.png")
    pack = json.loads((local / "pack.json").read_text())
    pack["roms"]["consoles"] = [
        {"id": "gba", "label": "Game Boy Advance (mine)", "extensions": ["*.gba"]},
        {"id": "gb", "label": "Game Boy (mine)", "extensions": ["*.gb", "*.gbc"]},
    ]
    (local / "pack.json").write_text(json.dumps(pack))

    r = _run_merge(code, data)
    assert r.returncode == 0, r.stderr
    live = json.loads((data / "config" / "systems.json").read_text())
    mgba = next(s for s in live if s["id"] == "mgba")
    assert [c["id"] for c in mgba["consoles"]] == ["gba", "gb"], \
        "the operator's catalog.d pack under the DATA root was not honoured"
