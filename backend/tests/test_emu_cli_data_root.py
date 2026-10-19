"""`gamecore-emu` on a box whose data does not live inside the install.

Finding 12 of the 2026-09-04 complementary audit. The script has declared
`GAMECORE_DATA` since the split, and then read and wrote `GAMECORE_PATH/config`
anyway: the grid, the operator's own packs and the list of declined packs all
came from the code tree, which on such a box is not where the backend looks.

`gamecore-emu remove azahar` therefore *succeeded* — printed its lines, exited
0 — having rewritten a `systems.json` nothing reads. The tile stayed on the
console's screen, and there was nothing in the log to say why.

The real script is run here, against two temporary trees, with the repository's
own `backend/`, `catalog/` and `scripts/` linked into the code root. Nothing is
installed: `remove` and the grid merge touch JSON files and nothing else.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux_until_port(7)

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "install" / "bin" / "gamecore-emu"


def _trees(tmp_path: Path, *, split: bool = True) -> tuple[Path, Path]:
    """A code root and a data root, each with a grid of its own."""
    code = tmp_path / "code"
    data = tmp_path / "data" if split else code
    (code / "config").mkdir(parents=True)
    (data / "config").mkdir(parents=True, exist_ok=True)
    for name in ("backend", "catalog", "scripts", ".venv"):
        source = REPO / name
        if source.exists():
            (code / name).symlink_to(source, target_is_directory=True)
    for root in {code, data}:
        (root / "config" / "systems.json").write_text(json.dumps([{"id": "azahar"}]))
        (root / "config" / "apps.json").write_text("[]")
    return code, data


def _fake_systemctl(tmp_path: Path, environment: str = "") -> Path:
    """A `systemctl` on PATH that answers `show -p Environment --value`.

    Not a nicety, and the same stub test_addon_contract.py keeps for the same
    reason. With no GAMECORE_DATA in the environment the CLI asks systemd for
    the backend unit's — which on a developer's machine is the real box's data
    root, so a test exercising that fallback edits the grid of the console it
    is running on. It did, once, while this file was being written; conftest.py
    now exports GAMECORE_DATA for the whole suite as the second line of
    defence.
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "systemctl"
    fake.write_text("#!/usr/bin/env bash\n"
                    "case \"$*\" in\n"
                    f"  *'-p Environment'*) printf '%s\\n' '{environment}' ;;\n"
                    "  *) exit 0 ;;\n"
                    "esac\n")
    fake.chmod(0o755)
    return bin_dir


def _run(args: list[str], code: Path, data: Path | None,
         *, tmp_path: Path | None = None, unit_says: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, "GAMECORE_PATH": str(code),
           "GAMECORE_USER": os.environ.get("USER", "nobody")}
    if data is not None:
        env["GAMECORE_DATA"] = str(data)
    else:
        # The fallback chain, with a systemd that only says what is asked of it.
        env.pop("GAMECORE_DATA", None)
        assert tmp_path is not None, "the fallback path needs a fake systemctl"
        env["PATH"] = f"{_fake_systemctl(tmp_path, unit_says)}:{env['PATH']}"
    return subprocess.run(["bash", str(CLI), *args], env=env, text=True,
                          capture_output=True, timeout=120)


def _ids(path: Path) -> list[str]:
    return [r["id"] for r in json.loads(path.read_text())]


def test_remove_takes_the_tile_off_the_grid_the_backend_reads(tmp_path):
    code, data = _trees(tmp_path)
    r = _run(["remove", "azahar"], code, data)
    assert r.returncode == 0, r.stderr

    assert _ids(data / "config" / "systems.json") == [], (
        "the grid the backend reads still has the pack — this is the whole defect")
    # And the copy in the install is not what was edited instead.
    assert _ids(code / "config" / "systems.json") == ["azahar"]
    assert json.loads((data / "config" / "catalog-removed.json").read_text()) == ["azahar"]
    assert not (code / "config" / "catalog-removed.json").exists()


def test_remove_still_works_where_the_two_roots_are_one_directory(tmp_path):
    """Every box installed before the split, and the default to this day."""
    code, _ = _trees(tmp_path, split=False)
    r = _run(["remove", "azahar"], code, None, tmp_path=tmp_path, unit_says="")
    assert r.returncode == 0, r.stderr
    assert _ids(code / "config" / "systems.json") == []
    assert json.loads((code / "config" / "catalog-removed.json").read_text()) == ["azahar"]


def test_the_data_root_is_taken_from_the_backend_unit_when_nobody_says(tmp_path):
    """How the backend's own calls arrive: `sudo -n gamecore-emu …`, no env.

    sudo hands the script a clean environment, so the service's GAMECORE_DATA
    does not survive the hop. The unit is where it is written down, and asking
    systemd for it is what makes `remove` from the Systems screen edit the grid
    the console actually reads.
    """
    code, data = _trees(tmp_path)
    r = _run(["remove", "azahar"], code, None, tmp_path=tmp_path,
             unit_says=f"GAMECORE_PATH={code} GAMECORE_BACKEND_PORT=8765 GAMECORE_DATA={data}")
    assert r.returncode == 0, r.stderr
    assert _ids(data / "config" / "systems.json") == []
    assert _ids(code / "config" / "systems.json") == ["azahar"]


def test_list_reports_what_is_on_the_grid_the_player_sees(tmp_path):
    code, data = _trees(tmp_path)
    (data / "config" / "systems.json").write_text(json.dumps([{"id": "melonds"}]))
    r = _run(["list", "--json"], code, data)
    assert r.returncode == 0, r.stderr
    rows = {row["id"]: row["installed"] for row in json.loads(r.stdout)}
    assert rows["melonds"] is True
    assert rows["azahar"] is False, "the install's grid was read instead of the player's"


def test_the_grid_merge_writes_where_the_backend_reads_and_honours_a_removal(tmp_path):
    """`refresh_grid`, run on its own — the install path's last step."""
    code, data = _trees(tmp_path)
    (data / "config" / "systems.json").write_text("[]")
    (data / "config" / "catalog-removed.json").write_text(json.dumps(["azahar"]))
    (code / "config" / "systems.json").write_text("[]")

    # The merge adds a new system only once its emulator is installed, and asks
    # flatpak and pacman which are. Stubbed: Dolphin is here, nothing else.
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "flatpak").write_text("#!/bin/sh\necho org.DolphinEmu.dolphin-emu\n")
    (stubs / "pacman").write_text("#!/bin/sh\nexit 1\n")
    for f in stubs.iterdir():
        f.chmod(0o755)

    source = CLI.read_text().split("# ── dispatch")[0] + "\nrefresh_grid\n"
    r = subprocess.run(["bash"], input=source, text=True, capture_output=True, timeout=120,
                       env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}",
                            "GAMECORE_PATH": str(code),
                            "GAMECORE_DATA": str(data),
                            "GAMECORE_USER": os.environ.get("USER", "nobody")})
    assert r.returncode == 0, r.stderr

    live = _ids(data / "config" / "systems.json")
    assert "gamecube" in live, "nothing reached the grid the backend reads"
    assert "dolphin" not in live, "a superseded pack was added to a fresh grid"
    assert "nes" not in live, "a system whose emulator is absent reached the grid"
    assert "azahar" not in live, (
        "a declined pack came back: the removal list was read from the other tree")
    assert _ids(code / "config" / "systems.json") == []


def test_installing_lifts_a_removal_recorded_on_the_data_side(tmp_path):
    """Only the un-decline step — nothing is obtained, so `install` stops there."""
    code, data = _trees(tmp_path)
    (data / "config" / "catalog-removed.json").write_text(json.dumps(["azahar"]))

    # The provider is what obtains a Flatpak; nothing is installed here, so it
    # is replaced by a file that does nothing. The un-decline step above it is
    # the real one, and it is the step under test.
    stub = tmp_path / "provider.py"
    stub.write_text("import sys\n")
    source = CLI.read_text().split("# ── dispatch")[0] + f"""
PROVIDER={stub}
apply_pack() {{ :; }}
refresh_grid() {{ :; }}
cmd_install azahar
"""
    r = subprocess.run(["bash"], input=source, text=True, capture_output=True, timeout=120,
                       env={**os.environ, "GAMECORE_PATH": str(code),
                            "GAMECORE_DATA": str(data),
                            "GAMECORE_USER": os.environ.get("USER", "nobody")})
    assert json.loads((data / "config" / "catalog-removed.json").read_text()) == [], r.stderr
