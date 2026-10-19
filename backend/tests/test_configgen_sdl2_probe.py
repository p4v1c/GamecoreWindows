"""`sdl2_probe` — telling "SDL said no" apart from "SDL was never asked".

The reference box logged `SDL2 probe failed for 045e:02fd` three times over
several days, each at the instant a Bluetooth Xbox pad connected, and the
player was told *SDL2 would not report a GUID for 045e:02fd*. Those are not
the same sentence: the probe subprocess had raised, so SDL never answered at
all. Measured minutes afterwards, the same host SDL2 and the same libSDL2.so
bundled with Ryujinx both returned `050018dc5e040000fd02000003090000` — ten
runs out of ten, 0.85 s each, against an 8 s timeout.

Both facts used to leave `sdl2_probe` as an empty dict, which is why the
give-up could only name the commoner one, and why the errno behind those three
lines is now unrecoverable: the handler discarded the exception too.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.configgen import controllers as cc        # noqa: E402
from backend.services.configgen import sdl_probe as sp          # noqa: E402


def test_a_probe_that_could_not_run_is_not_an_answer_about_the_pad(monkeypatch):
    """An unrunnable probe must be distinguishable from an SDL that answered.

    `SDL_NumJoysticks` returning no match is a finding about the pad. A
    subprocess that never ran is a finding about this box, and the two call for
    different things — the mapping wizard against a retry.
    """
    sp._sdl2_cache.clear()
    # EAGAIN, which is what a fork that cannot get a process slot raises — and
    # Python specialises it to BlockingIOError, so the reported class is read
    # off the exception rather than spelled out here.
    failure = OSError(11, "Try again")
    monkeypatch.setattr(sp.subprocess, "run", _raising(failure))

    out = sp.sdl2_probe("045e", "02fd")

    assert out.get("guid", "") == "", "no GUID either way — that part is unchanged"
    assert out.get("error") == type(failure).__name__, (
        "the caller cannot say what happened if both failures look identical")


def test_a_timeout_is_reported_as_a_failure_to_run_not_as_a_missing_pad():
    """`TimeoutExpired` is a SubprocessError, not an OSError.

    Named separately because the two arrive through the same `except` clause
    and only one of them was ever suspected: the reference box's failures came
    1.8 s after the trigger, against an 8 s timeout, so they were NOT timeouts
    — and nothing in the log could have established that at the time.
    """
    sp._sdl2_cache.clear()
    real_run = sp.subprocess.run
    try:
        sp.subprocess.run = _raising(subprocess.TimeoutExpired("python", 8))
        out = sp.sdl2_probe("045e", "02fd")
    finally:
        sp.subprocess.run = real_run

    assert out.get("error") == "TimeoutExpired"


def test_a_failed_probe_is_not_remembered(monkeypatch):
    """A failure must not be cached, or the retry budget is spent on a memory.

    `sdl2_probe` caches for 5 s and gamepad_monitor re-profiles every ~3 s, so
    caching a failure would hand the next attempts the same non-answer without
    ever asking SDL again — and the whole PROFILE_RETRIES budget could burn
    inside one cache window.
    """
    sp._sdl2_cache.clear()
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError(11, "Try again")

    monkeypatch.setattr(sp.subprocess, "run", boom)

    sp.sdl2_probe("045e", "02fd")
    sp.sdl2_probe("045e", "02fd")

    assert len(calls) == 2, "the second call read a cached failure"


def _raising(exc):
    def run(*a, **k):
        raise exc
    return run


# ── what the probe is allowed to be told ─────────────────────────────────────
#
# The second failure this file records, and the more expensive one, because it
# produced an answer instead of an absence.
#
# `sdl2_probe` is one of the two SOURCES `inputs.py` is built on: it is asked
# what SDL ITSELF knows about a pad, precisely where the owner's capture must
# not be believed. On the reference box it answered with the capture. SDL reads
# a mapping table from SDL_GAMECONTROLLERCONFIG_FILE and keeps the LAST line it
# finds for a GUID, so a table carrying the owner's line beats SDL's built-in —
# and the same DualShock 4, the same code, at the same instant:
#
#     probe in a fresh process     start:b6  back:b4  leftshoulder:b9   dpup:b11
#     probe in a backend that had  start:b9  back:b8  leftshoulder:b4   dpup:h0.1
#     enumerated a pad once
#
# The second line is the capture, in the LINUX JOYSTICK driver's numbering, for
# a pad SDL drives through HIDAPI. It reached azahar as `start` on that
# driver's L1 and a D-pad bound to a hat SDL calls buttons 11-14 — reported in
# game as "L1 = Options, the D-pad does not work". `evdev_driven()`
# answered False throughout: the guard was never wrong, it was bypassed.

def test_a_probe_is_never_handed_a_mapping_table(monkeypatch):
    """The fix, stated where it is enforced.

    Both variables, not only the one that leaked: SDL reads a table from either,
    and a rule with an exception is a rule someone re-derives wrongly later.
    """
    sp._sdl2_cache.clear()
    seen = {}

    def run(*a, **k):
        seen.update(k.get("env") or {})
        raise OSError(11, "Try again")     # the answer is not what is under test

    monkeypatch.setenv("SDL_GAMECONTROLLERCONFIG_FILE", "/tmp/served.txt")
    monkeypatch.setenv("SDL_GAMECONTROLLERCONFIG", "0500,Pad,a:b0,")
    monkeypatch.setattr(sp.subprocess, "run", run)

    sp.sdl2_probe("054c", "09cc")

    assert "SDL_GAMECONTROLLERCONFIG_FILE" not in seen
    assert "SDL_GAMECONTROLLERCONFIG" not in seen


def test_the_rest_of_the_environment_reaches_the_probe(monkeypatch):
    """Scrubbed, not replaced. The probe still needs this box's PATH and
    LD_LIBRARY_PATH to load an emulator's bundled libSDL2.so at all."""
    monkeypatch.setenv("GAMECORE_TEST_MARKER", "kept")

    assert sp.probe_env().get("GAMECORE_TEST_MARKER") == "kept"


def test_enumerating_a_pad_does_not_change_what_a_later_probe_is_told(monkeypatch):
    """The leak itself: `os.environ.setdefault` outlives the call that made it.

    `_sdl3_live_names` genuinely wants the served table — a NAME must match what
    the emulators enumerate — and it took it by mutating the backend's own
    environment, which is what every later subprocess inherits. The bug was
    therefore ORDER-DEPENDENT: whether azahar got SDL's numbers or the owner's
    depended on whether a pad had been enumerated earlier in that process.
    """
    from backend.services.configgen import mapping_db

    monkeypatch.delenv("SDL_GAMECONTROLLERCONFIG_FILE", raising=False)
    served = Path("/tmp/served.txt")
    monkeypatch.setattr(mapping_db, "served", lambda: served)

    with cc._served_db_in_env():
        inside = sp.os.environ.get("SDL_GAMECONTROLLERCONFIG_FILE")

    assert inside == str(served), "the name lookup still gets its table"
    assert "SDL_GAMECONTROLLERCONFIG_FILE" not in sp.os.environ, (
        "the table outlived the call, and the next probe inherits it")


# ── a bad second at boot must not last the session ───────────────────────────

def test_a_failed_flatpak_lookup_is_not_cached(monkeypatch):
    """One `flatpak info` that fails used to answer "" for the life of the
    process, and every consumer then took the branch meant for "not installed".

    Measured cost: `bundled_sdl3` returned "", its caller fell through to the
    HOST's libSDL3, and RMG got a DualSense profile whose serial the library it
    actually links spells differently — `50-ee-32-32-88-2d` against
    `50:ee:32:32:88:2d`. A pad bound to nothing until the backend restarted.

    A miss costs one `flatpak info`, 14 ms on the reference box. That is not
    worth a session of wrong configs.
    """
    sp._flatpak_loc_cache.clear()
    calls = []

    class R:
        def __init__(self, rc, out): self.returncode, self.stdout = rc, out

    def run(cmd, **kw):
        calls.append(cmd)
        return R(1, "") if len(calls) == 1 else R(0, "/deploy/rmg\n")

    monkeypatch.setattr(sp.subprocess, "run", run)

    assert sp.flatpak_location("com.example.App") == ""
    assert sp.flatpak_location("com.example.App") == "/deploy/rmg"
    assert len(calls) == 2, "the failure was cached and never retried"


def test_a_successful_flatpak_lookup_is_cached(monkeypatch):
    """The retry must not become a subprocess on every profiling pass."""
    sp._flatpak_loc_cache.clear()
    calls = []

    class R:
        def __init__(self): self.returncode, self.stdout = 0, "/deploy/rmg\n"

    monkeypatch.setattr(sp.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), R())[1])

    assert sp.flatpak_location("com.example.App") == "/deploy/rmg"
    assert sp.flatpak_location("com.example.App") == "/deploy/rmg"
    assert len(calls) == 1


@pytest.mark.linux_until_port(5)
def test_two_installed_branches_resolve_to_the_one_flatpak_run_starts(monkeypatch, tmp_path):
    """A local Eden build (branch master) next to Flathub's (stable) made
    `flatpak info dev.eden_emu.eden` fail with "multiple branches". The runtime
    lookup then answered "", and the generator probed the host's SDL instead of
    Eden's, in silence. The branch is read from the app's `current` link."""
    sp._flatpak_loc_cache.clear()
    sp._runtime_loc_cache.clear()
    app = tmp_path / "flatpak/app/dev.eden_emu.eden"
    app.mkdir(parents=True)
    (app / "current").symlink_to("x86_64/master")
    monkeypatch.setattr(sp, "_INSTALLATIONS", (tmp_path / "flatpak",))

    class R:
        def __init__(self, rc, out): self.returncode, self.stdout = rc, out

    answers = {
        ("--show-location", "dev.eden_emu.eden//master"): "/deploy/eden-master",
        ("--show-runtime", "dev.eden_emu.eden//master"): "org.kde.Platform/x86_64/6.11",
        ("--show-location", "runtime/org.kde.Platform/x86_64/6.11"): "/deploy/kde",
    }
    monkeypatch.setattr(sp.subprocess, "run", lambda cmd, **kw: (
        R(0, answers[tuple(cmd[2:])] + "\n") if tuple(cmd[2:]) in answers else R(1, "")))

    assert sp.flatpak_location("dev.eden_emu.eden") == "/deploy/eden-master"
    assert sp.flatpak_runtime_location("dev.eden_emu.eden") == "/deploy/kde"
