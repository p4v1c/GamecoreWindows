"""Leaving the console is a trip, not a move.

The interface's exit to the desktop is an entry in the power menu, between
Restart and Shut down, and it is called `desktop`. What it did was rewrite the
display manager's auto-login — persistently, for every boot after it. So a
player who wanted their desktop for ten minutes got it for ever, and coming
back meant knowing the name of a command.

Measured on the reference box: three boots in one day landing on Plasma, each
because a single exit hours earlier had changed how the machine starts. The
owner's own reading of it — "I write in a file to go to desktop mode, and when
I reboot it keeps that" — is exactly right, and is the whole defect.

The switch still has to happen: the display manager is restarted on the spot
and has to be pointed somewhere for the player to land. What changes is that
`--once` leaves a marker, and `gamecore-rearm-console.service` reads it before
the display manager starts at the next boot and puts the console back.

`desktop` without `--once` is untouched. "Stop opening the console" is a real
thing to want and keeps its own command.

Nothing here starts a display manager or writes outside a staging tree.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SELECT = ROOT / "install" / "bin" / "gamecore-session-select"
SESSION = ROOT / "install" / "bin" / "gamecore-session"
STEP = ROOT / "install" / "steps" / "setup-gamecore-session.sh"
UNIT = ROOT / "install" / "system" / "gamecore-rearm-console.service"


@pytest.fixture
def switch(tmp_path):
    """The selector, against a staging /etc and a staging state directory."""
    sddm = tmp_path / "etc" / "sddm.conf.d"
    xses = tmp_path / "usr" / "share" / "xsessions"
    sddm.mkdir(parents=True)
    xses.mkdir(parents=True)
    bins = tmp_path / "bin"
    bins.mkdir()
    for name in ("systemctl", "fake-wm"):
        (bins / name).write_text("#!/bin/sh\nexit 0\n")
        (bins / name).chmod(0o755)
    (xses / "plasmax11.desktop").write_text("[Desktop Entry]\nName=Plasma (X11)\n")
    (xses / "gamecore.desktop").write_text("[Desktop Entry]\nName=GameCore\n")
    state = tmp_path / "state"

    def run(*args):
        return subprocess.run(
            ["bash", str(SELECT), *args],
            env={**os.environ, "SDDM_CONF_DIR": str(sddm), "XSESSIONS_DIR": str(xses),
                 "WAYLAND_SESSIONS_DIR": str(tmp_path / "none"),
                 "GAMECORE_USER": "player", "GAMECORE_USER_HOME": str(tmp_path / "home"),
                 "SYSTEM_UNIT_DIR": str(tmp_path / "units"),
                 "GAMECORE_WM_CANDIDATES": "fake-wm",
                 "GAMECORE_STATE_DIR": str(state),
                 "PATH": str(bins) + ":" + os.environ["PATH"]},
            text=True, capture_output=True, timeout=60)

    def session_of() -> str:
        conf = sddm / "zz-gamecore-autologin.conf"
        for line in conf.read_text().splitlines() if conf.is_file() else []:
            if line.startswith("Session="):
                return line.split("=", 1)[1]
        return ""

    return {"run": run, "session": session_of, "marker": state / "rearm-console",
            "state": state, "sddm": sddm}


# ── the trip ────────────────────────────────────────────────────────────────

@pytest.mark.linux_until_port(7)
def test_a_trip_points_the_display_manager_at_the_desktop(switch):
    """It still has to switch. The player is landing on the desktop in a
    second, and the display manager has to have been told where."""
    switch["run"]("gamecore")
    r = switch["run"]("desktop", "--once")
    assert r.returncode == 0, r.stderr
    assert switch["session"]() == "plasmax11"


@pytest.mark.linux_until_port(7)
def test_a_trip_leaves_something_to_come_back_by(switch):
    switch["run"]("gamecore")
    switch["run"]("desktop", "--once")
    assert switch["marker"].is_file(), (
        "nothing recorded that this was a trip, so the next boot has no way to "
        "know the console was meant to come back")


@pytest.mark.linux_until_port(7)
def test_a_trip_says_it_is_temporary(switch):
    """The message is the only place a player learns which of the two they
    just chose."""
    switch["run"]("gamecore")
    r = switch["run"]("desktop", "--once")
    assert "this session only" in r.stdout, r.stdout
    assert "next boot opens GameCore" in r.stdout, r.stdout


# ── the move, which is a different thing and must stay ──────────────────────

@pytest.mark.linux_until_port(7)
def test_a_plain_desktop_switch_is_still_permanent(switch):
    """"Stop opening the console" is a real thing to want. It keeps its own
    command, and it leaves no marker to undo it."""
    switch["run"]("gamecore")
    r = switch["run"]("desktop")
    assert r.returncode == 0, r.stderr
    assert switch["session"]() == "plasmax11"
    assert not switch["marker"].exists(), "a permanent move left a return marker"


@pytest.mark.linux_until_port(7)
def test_a_move_cancels_a_trip_that_had_not_happened_yet(switch):
    """The player has now said the opposite of what the marker remembers."""
    switch["run"]("gamecore")
    switch["run"]("desktop", "--once")
    assert switch["marker"].is_file()

    switch["run"]("desktop")
    assert not switch["marker"].exists(), (
        "the box would have re-armed the console despite being told to stay "
        "on the desktop")


@pytest.mark.linux_until_port(7)
def test_arming_by_hand_settles_a_pending_trip(switch):
    """Nothing left to put back — the console is on. A marker surviving that
    re-arms an already-armed box and puzzles the next person to read
    /var/lib/gamecore."""
    switch["run"]("gamecore")
    switch["run"]("desktop", "--once")
    switch["run"]("gamecore")
    assert switch["session"]() == "gamecore"
    assert not switch["marker"].exists()


@pytest.mark.linux_until_port(7)
def test_the_round_trip_ends_where_it_started(switch):
    """Console, trip to the desktop, re-arm: the box is back to opening the
    console and remembers nothing about the trip."""
    switch["run"]("gamecore")
    switch["run"]("desktop", "--once")
    assert switch["session"]() == "plasmax11"

    # What the unit does at the next boot.
    switch["run"]("gamecore", "--force")
    assert switch["session"]() == "gamecore"
    assert not switch["marker"].exists()


# ── the unit that performs the return ───────────────────────────────────────

def test_the_unit_runs_before_the_display_manager():
    """The only moment a boot can be redirected. SDDM reads its configuration
    when it starts and never again."""
    unit = UNIT.read_text()
    assert "Before=display-manager.service" in unit
    assert "Type=oneshot" in unit


def test_the_unit_does_nothing_unless_a_trip_is_pending():
    """It must be inert on a box that never left the console, and on one that
    left it deliberately."""
    unit = UNIT.read_text()
    assert "ConditionPathExists=/var/lib/gamecore/rearm-console" in unit


def test_the_unit_clears_the_marker_even_when_it_fails():
    """A marker that survives its own failure re-arms the box on every boot
    from now on — a worse fault than the one it fixes."""
    unit = UNIT.read_text()
    assert "ExecStopPost=" in unit and "rm -f /var/lib/gamecore/rearm-console" in unit


# ── the two callers, and what the installer grants them ─────────────────────

def test_the_session_teardown_takes_the_trip():
    """The interface's exit goes through here. If it still asked for a plain
    `desktop`, everything above would be dead code."""
    assert "gamecore-session-select desktop --once --restart-dm" in SESSION.read_text()


def test_the_electron_fallback_takes_it_too():
    """The way out for a box whose session script predates the marker."""
    main = (ROOT / "electron" / "main.js").read_text()
    assert "desktop --once --restart-dm" in main


@pytest.mark.linux_until_port(7)
def test_the_trip_is_granted_without_a_password(tmp_path):
    """sudoers matches a command line exactly, so `--once` needs its own line.
    Without it the exit asks for a password a television cannot type."""
    (tmp_path / "home" / "player").mkdir(parents=True)
    r = subprocess.run(
        ["bash", str(STEP), "player", "/opt/GameCore", "/userdata", "8765"],
        env={**os.environ, "DESTDIR": str(tmp_path), "GAMECORE_USER_HOME": "/home/player"},
        text=True, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr

    rules = (tmp_path / "etc" / "sudoers.d" / "gamecore-session").read_text()
    assert "gamecore-session-select desktop --once --restart-dm" in rules, rules
    # And the permanent move keeps its own grant.
    assert "gamecore-session-select desktop --restart-dm" in rules


@pytest.mark.linux_until_port(7)
def test_the_installer_puts_the_unit_where_it_will_run(tmp_path):
    (tmp_path / "home" / "player").mkdir(parents=True)
    r = subprocess.run(
        ["bash", str(STEP), "player", "/opt/GameCore", "/userdata", "8765"],
        env={**os.environ, "DESTDIR": str(tmp_path), "GAMECORE_USER_HOME": "/home/player"},
        text=True, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr

    installed = tmp_path / "etc" / "systemd" / "system" / "gamecore-rearm-console.service"
    assert installed.is_file(), "the unit was not installed"
    link = (tmp_path / "etc" / "systemd" / "system" / "graphical.target.wants"
            / "gamecore-rearm-console.service")
    assert link.is_symlink() or link.is_file(), "installed but never enabled"
    # The marker's home, so the first trip has somewhere to write.
    assert (tmp_path / "var" / "lib" / "gamecore").is_dir()
