"""The suite must not be able to write the developer's own emulator configs.

Measured, not hypothetical: a `pytest` run rewrote Player 1 of this machine's
RPCS3 `Default.yml` and emptied Ryujinx's `input_config`. `TestClient(main.app)`
runs the app lifespan, the lifespan starts `gamepad_monitor.run()`, and the
monitor scans the REAL `/dev/input`, finds whatever pad the developer left
plugged in, and profiles it against `configgen.HOME` — `Path.home()`, read at
import time. `conftest.py` now points `HOME` at the throwaway root before any
import; nothing guarded that it stays pointed there.

An earlier audit pass asked this exact question and cleared it. Its evidence was
a run under a sentinel `HOME` that came back empty — but the write only happens
when a pad is connected, and none was: an experiment that could not fail
whatever the code did. Neither test below depends on what is plugged in.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import win32api
import win32con
import win32profile
import win32security

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import configgen, pergame  # noqa: E402

# The suite's own fresh temp root (conftest.py). %TEMP% sits inside the real
# profile on Windows, so "outside the real home" cannot hold; "inside this
# root" is the stronger property anyway.
TEST_ROOT = Path(os.environ["GAMECORE_TEST_ROOT"]).resolve()

# Below the real profile, where Windows puts them unless a policy moved them.
REAL_SUBFOLDER = {"USERPROFILE": ".", "APPDATA": "AppData/Roaming",
                  "LOCALAPPDATA": "AppData/Local"}


def _real(var: str) -> Path:
    """The account's own folder, from the process token. Not from the
    environment, and not from SHGetKnownFolderPath, which expands
    %USERPROFILE% from it: the variables are the thing under test."""
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                           win32con.TOKEN_QUERY)
    profile = Path(win32profile.GetUserProfileDirectory(token))
    return (profile / REAL_SUBFOLDER[var]).resolve()


def test_the_home_the_generators_write_to_is_not_the_developers():
    real = _real("USERPROFILE")
    home = configgen.HOME.resolve()

    assert home != real, (
        f"configgen.HOME is the real account home ({real}). Every emulator "
        f"config this suite touches is the box's own.")
    assert TEST_ROOT in home.parents, (
        f"configgen.HOME ({home}) is not inside the suite's throwaway root ({TEST_ROOT}).")


@pytest.mark.parametrize("var", sorted(REAL_SUBFOLDER))
def test_the_windows_home_folders_are_not_the_developers(var):
    """`Path.home()` reads USERPROFILE on Windows, and Windows emulators keep
    their configs and saves under APPDATA and LOCALAPPDATA."""
    folder = Path(os.environ[var]).resolve()
    assert folder != _real(var), (
        f"{var} is the real account's ({folder}): a test writing there edits "
        f"this PC's own emulator configs.")
    assert TEST_ROOT in folder.parents, (
        f"{var} ({folder}) is not inside the suite's throwaway root ({TEST_ROOT}).")


def test_every_profilable_pack_writes_below_that_home():
    """What makes the single line in conftest sufficient rather than lucky.

    `HOME` is the root every generator's target is built from, so redirecting it
    redirects all of them at once. A pack that resolved its config dir some
    other way would be a second door, and this is the test that would find it.
    """
    packs = configgen.profilable_packs(configgen.load_catalog())
    assert packs, "no pack profiles controllers — the loop below would assert nothing"

    home = configgen.HOME.resolve()
    for pack in packs:
        opts = configgen.generator_opts(pack, configgen.HOME, configgen.SNAP_DIR)
        assert opts is not None, f"{pack.id}: no config dir resolved"
        target = opts["target"].resolve()
        assert home in target.parents, (
            f"{pack.id} writes to {target}, outside the throwaway home {home}")


def test_every_per_game_path_is_built_from_a_home_that_can_be_redirected():
    """A second family of writes into `~/.var/app`, and the same danger.

    The controller generators all resolve their target from one `HOME`, which
    is what makes the single line in conftest sufficient. The per-game writer
    is newer and takes `home` as an ARGUMENT instead — a deliberate choice, so
    a caller cannot forget which tree it is aiming at, but it moves the
    guarantee from "one variable" to "every caller passes the right thing".

    This is the test that keeps it honest: hand every supporting pack a
    sentinel home and check the path it produces actually lands under it. A
    pack whose `perGame.path` reached the real tree some other way — an
    absolute path, a `~`, a token that does not expand — would be a second
    door onto the developer's own console, which is the exact thing this file
    was written after somebody walked through.
    """
    from backend.services.catalog import load_catalog

    sentinel = Path("/tmp/gamecore-sentinel-home")
    supporting = [p for p in load_catalog().values()
                  if (p.data.get("perGame") or {}).get("supported")]
    assert supporting, "no pack supports per-game config — this asserts nothing"

    for pack in supporting:
        target = pergame.target(pack.id, "TESTID", sentinel)
        assert target is not None, f"{pack.id}: no per-game path resolved"
        assert sentinel in target.parents, (
            f"{pack.id} writes per-game settings to {target}, which is not "
            f"under the home it was handed ({sentinel})")
