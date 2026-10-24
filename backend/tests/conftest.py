"""Shared pytest setup for the backend test suite.

The one thing that must happen before anything else: point GAMECORE_PATH at a
throwaway directory. `backend.config` reads it at import time and every other
path — covers cache, config/, playtime.db — is derived from it, so whichever
test module pytest imports first would otherwise bind the suite to the real
checkout (or worse, to whatever GAMECORE_PATH the shell already exported) and
the cover tests would scribble their .miss markers into it.

conftest.py is imported before any test module, which makes this the only place
the override is guaranteed to land in time. GAMECORE_TEST_ROOT is the handshake:
test_covers.py builds its fake game tree there instead of minting its own root.
"""
import os
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_ROOT = Path(tempfile.mkdtemp(prefix="gamecore-test-")) / "fake_root"
_ROOT.mkdir(parents=True, exist_ok=True)

# Set, never defaulted: inheriting a GAMECORE_PATH from the environment would
# aim the suite at a real installation.
os.environ["GAMECORE_PATH"] = str(_ROOT)
os.environ["GAMECORE_TEST_ROOT"] = str(_ROOT)

# And the data root with it, for the same reason and with a sharper edge.
#
# `paths.py` defaults GAMECORE_DATA to GAMECORE_PATH, so leaving it unset was
# harmless for anything importing the backend. It is not harmless for the two
# CLIs: `gamecore-emu` and `gamecore-addon` are installed to /usr/local/bin and
# have no checkout to read, so when the variable is absent they ask
# `systemctl show gamecore-backend.service` for it — and on a developer's own
# box that answers with the REAL data root. A test that runs either of them
# without this line edits the grid of the machine it is running on. It is not
# hypothetical: it happened while the CLI's own tests were being written, and
# the console lost an emulator to a passing `pytest`.
os.environ["GAMECORE_DATA"] = str(_ROOT)

# And the runtime directory: the Linux session scripts still tested in bash
# (steps 7-8) look there, and must find the suite's, never the machine's.
os.environ["XDG_RUNTIME_DIR"] = str(_ROOT.parent / "runtime")
(_ROOT.parent / "runtime").mkdir(parents=True, exist_ok=True)

# Same rule for the ScreenScraper account. gamescrape reads the four variables
# below, then falls back to ~/.config/gamescrape/credentials — a developer's own
# file, which would make the suite behave differently on their machine than in
# CI. It matters more than it looks: with credentials present, the gamemedia
# tier is enabled, cannot reach the network from a test, and correctly reports
# "unreachable" — which suppresses the .miss marker test_covers asserts on. The
# suite's baseline is an unconfigured box; a test that wants the tier enables it
# itself.
for _var in ("SCREENSCRAPER_DEV_ID", "SCREENSCRAPER_DEV_PASSWORD",
             "SCREENSCRAPER_USER", "SCREENSCRAPER_PASSWORD"):
    os.environ.pop(_var, None)
os.environ["XDG_CONFIG_HOME"] = str(_ROOT / "config-home")

# HOME, for the same reason and with a sharper edge. `configgen.HOME` is
# `Path.home()` evaluated at IMPORT time, and it is the root of every emulator
# config path the generators write to: ~/.var/app/<appId>/config/…
#
# Nothing calls a generator directly under test — the characterisation harness
# patches `configgen.HOME` at a fake tree. But `TestClient(main.app)` runs the
# app's lifespan, and the lifespan starts `gamepad_monitor.run()`, which scans
# the REAL /dev/input, finds whatever pad the developer has plugged in, and
# profiles it against the REAL home. Measured on this machine: a test-suite run
# rewrote Player 1 of ~/.var/app/net.rpcs3.RPCS3/…/Default.yml and emptied
# Ryujinx's input_config. The developer's own console, edited by pytest.
#
# The seam is one line because the damage is one line. Set before any import,
# like GAMECORE_PATH above, since Path.home() is read at module scope.
#
# On Windows `Path.home()` and `expanduser` read USERPROFILE, never HOME, and
# emulators keep their configs under APPDATA / LOCALAPPDATA: all four point
# into the throwaway root, or the line above guards nothing.
_HOME = _ROOT / "home"
for _var, _sub in (("HOME", ""), ("USERPROFILE", ""),
                   ("APPDATA", "AppData/Roaming"), ("LOCALAPPDATA", "AppData/Local")):
    os.environ[_var] = str(_HOME / _sub)
    (_HOME / _sub).mkdir(parents=True, exist_ok=True)

# The catalogue is SHIPPED CODE, not box state: catalog/<id>/pack.json is the
# single source scraper.py, gamemedia.py and the installers read. The throwaway
# root above exists to keep writable data (covers, playtime.db, config/) out of
# the checkout — it must not also hide the catalogue, or every consumer that
# builds its tables at import time would come up empty under test and the
# suite would be green about maps that are simply absent.
_REPO = Path(__file__).resolve().parents[2]
# A junction, not a symlink: symlinks need admin on Windows (WinError 1314).
from backend.services.platform import filesystem as _filesystem  # noqa: E402

_filesystem.link_dir(_ROOT / "catalog", _REPO / "catalog")


# ── Linux code the port has not replaced yet ─────────────────────────────────
import pytest as _pytest  # noqa: E402

from backend.tests.posix_boundary import posix_boundary  # noqa: E402,F401  (fixture)
from backend.tests.fake_jobs import spawner  # noqa: E402,F401  (fixture)
from backend.tests.fake_display import fake_screen  # noqa: E402,F401  (fixture)
from backend.tests.fake_volumes import disks  # noqa: E402,F401  (fixture)


def pytest_collection_modifyitems(config, items):
    """`linux_until_port(step)`: skipped on Windows, step named (linux_until_port.py)."""
    from backend.tests.linux_until_port import reason

    for item in items:
        mark = item.get_closest_marker("linux_until_port")
        if mark is None:
            continue
        message = reason(mark.args[0] if mark.args else None)
        if sys.platform == "win32":
            item.add_marker(_pytest.mark.skip(reason=message))


# ── The box is not a test bench: no screen, no plan ─────────────────────────
#
# `TestClient(main.app)` runs the lifespan, which turns the screen on and
# restores the power plan (`standby.resume_after_restart()`) and claims
# Windows' idle timers (`desktop_power.claim()`). Stubbed at `standby._power`,
# standby's only way to Windows; the timers by the machine guard below.
# test_standby_switch, test_standby_launch and test_standby_dark add their own
# function-scoped patch on top to observe the attempts.


@_pytest.fixture(autouse=True, scope="session")
def _no_real_commands_from_the_suite():
    from backend.services import standby

    async def refuse(*argv, **kw):
        return True          # "it worked": standby treats failure as best effort

    original = standby._power
    standby._power = refuse
    yield
    standby._power = original


# ── This PC is not a test bench: no platform call that changes it ────────────
#
# The Windows calls that change the machine (display mode, screen power, power
# plan and idle timers, disk eject, sleep, device wake settings) are replaced
# for the whole session; a test
# that needs one fakes it on top (`fake_display.fake_screen`,
# `fake_volumes.disks`). Reads stay real, except the idle timers (a claim would
# read the real plan, then write it), the disks (the lifespan's storage
# monitor would link the developer's USB drives into the test root) and the
# pads: SDL is never started, so the lifespan's monitor stops at once, as it
# did on a box without evdev, and never profiles the developer's own pad.
# Rest mode's lifespan tasks hear no real sleep (`sleep.watch` registers
# nothing) and read no wake setting, which unelevated lists nothing anyway.


@_pytest.fixture(autouse=True, scope="session")
def _no_machine_changes_from_the_suite():
    from backend.services.platform import device_wake as platform_device_wake
    from backend.services.platform import display as platform_display
    from backend.services.platform import gamepads as platform_gamepads
    from backend.services.platform import power as platform_power
    from backend.services.platform import sleep as platform_sleep
    from backend.services.platform import volumes as platform_volumes

    def no_screen(*args, **kwargs):
        return False, "no screen under test"

    def nothing(*args, **kwargs):
        return None

    def no_plan(*args, **kwargs):
        raise OSError("no power plan under test")

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(platform_display, "apply_mode", no_screen)
        for name in ("screen_on", "screen_off", "signal_activity"):
            mp.setattr(platform_power, name, nothing)
        for name in ("idle_timeouts", "set_idle_timeouts", "active_scheme", "set_active_scheme"):
            mp.setattr(platform_power, name, no_plan)
        mp.setattr(platform_volumes, "list_volumes", lambda: [])
        mp.setattr(platform_volumes, "eject", lambda letter: (False, "no disk under test"))
        mp.setattr(platform_gamepads, "start", lambda: False)
        mp.setattr(platform_gamepads, "pads", lambda: [])
        mp.setattr(platform_sleep, "suspend", lambda: (False, "no sleep under test"))
        mp.setattr(platform_sleep, "watch", lambda handler: (lambda: None))
        mp.setattr(platform_sleep, "last_wake", lambda: None)
        mp.setattr(platform_device_wake, "wake_devices", lambda: [])
        mp.setattr(platform_device_wake, "set_wake",
                   lambda name, allowed: no_plan())
        yield
