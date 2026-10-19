"""The console session as a console: no way out that a gamepad cannot undo.

Four defects, found on a reference box that had stopped booting into GameCore
and could not be told why from its own shell. They are one file because they
are one story — the box was in desktop mode, nothing said so, and every symptom
the owner reported followed from that:

  · `gamecore-session-select` wrote its auto-login drop-in under sudo's umask,
    so the file came out root-only. `status`, whose own usage says it needs no
    root, then skipped the one file that decides everything and reported the
    next `[Autologin]` down — on a Plasma box, `plasma`. An armed console
    reported as "desktop", and a disarmed one reported the same: the command
    could not be wrong out loud.
  · `gamecore-launcher` could only draw the interface OVER the desktop, and
    called that starting GameCore. It is not the console: the desktop's window
    manager still owns Alt+Tab and Meta, which is exactly how a player ends up
    looking at a desktop a gamepad cannot drive.
  · the console session itself started its window manager with whatever key
    bindings that machine's defaults carried — Alt+Tab, Alt+F4, Meta — so the
    real session was escapable too.
  · `gamecore-ui.service` carried `StartLimitIntervalSec`/`StartLimitBurst` in
    `[Service]`, where systemd ignores them with a warning. The restart bound
    its own comment describes did not exist.

Nothing here starts a compositor, a session or a display manager. The session
script runs for real against stubs; the switch runs against a staging tree.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SESSION = REPO / "install" / "bin" / "gamecore-session"
SELECT = REPO / "install" / "bin" / "gamecore-session-select"
LAUNCHER = REPO / "install" / "bin" / "gamecore-launcher"
UI_UNIT = REPO / "install" / "system" / "user" / "gamecore-ui.service"
STEP = REPO / "install" / "steps" / "setup-gamecore-session.sh"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    path.chmod(0o755)


# ── the window manager's keyboard ───────────────────────────────────────────

@pytest.fixture
def session(tmp_path):
    """The console session, with a window manager that only reports itself."""
    binds = tmp_path / "bin"
    binds.mkdir()
    runtime = tmp_path / "run"
    runtime.mkdir()
    seen = tmp_path / "wm.log"

    _stub(binds / "systemctl", "exit 0")
    # The window manager writes down the environment and the arguments it was
    # given, then exits cleanly so the session ends by itself.
    _stub(binds / "fake-wm", f"""
        {{
          echo "ARGS: $*"
          echo "XDG_CONFIG_HOME: ${{XDG_CONFIG_HOME:-<unset>}}"
        }} >> {seen}
        exit 0
    """)

    def run(candidates: str | None = None, pick_its_own: bool = False):
        env = {
            "PATH": f"{binds}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "DISPLAY": ":9",
            "XAUTHORITY": str(tmp_path / "cookie"),
            "XDG_RUNTIME_DIR": str(runtime),
            # The player's own configuration, which this must never touch.
            "XDG_CONFIG_HOME": str(tmp_path / "player-config"),
        }
        if not pick_its_own:
            env["GAMECORE_COMPOSITOR"] = str(binds / "fake-wm")
        if candidates is not None:
            env["GAMECORE_WM_CANDIDATES"] = candidates
        return subprocess.run(["bash", str(SESSION)], env=env, text=True,
                              capture_output=True, timeout=30)

    return {"run": run, "seen": seen, "runtime": runtime, "bin": binds,
            "player_config": tmp_path / "player-config"}


def _wm_log(session) -> str:
    return session["seen"].read_text() if session["seen"].exists() else ""


def _wm_config_dir(session) -> Path:
    return session["runtime"] / "gamecore" / "wm"


@pytest.mark.linux_until_port(7)
def test_the_window_manager_is_given_a_configuration_of_the_sessions_own(session):
    """Not the machine's defaults, which is where Alt+Tab comes from."""
    r = session["run"]()
    assert r.returncode == 0, r.stderr

    log = _wm_log(session)
    assert "XDG_CONFIG_HOME:" in log, "the window manager was never started"
    handed = [ln.split(": ", 1)[1] for ln in log.splitlines()
              if ln.startswith("XDG_CONFIG_HOME:")]
    assert handed, log
    assert handed[0].endswith("/gamecore/wm"), handed


@pytest.mark.linux_until_port(7)
def test_alt_tab_and_the_meta_key_are_taken_away(session):
    """The two the owner reported by name. `none,none,` is the documented way
    to say "no key"; leaving the line out keeps the compiled-in default."""
    session["run"]()
    shortcuts = (_wm_config_dir(session) / "kglobalshortcutsrc").read_text()

    assert "[kwin]" in shortcuts
    for action in ("Walk Through Windows", "Walk Through Windows (Reverse)",
                   "Window Close", "Window Operations Menu", "Kill Window",
                   "Show Desktop"):
        assert f"{action}=none,none," in shortcuts, f"{action} still has a key"

    kwinrc = (_wm_config_dir(session) / "kwinrc").read_text()
    assert "[ModifierOnlyShortcuts]" in kwinrc
    assert "Meta=" in kwinrc, "Meta on its own still opens a launcher"


@pytest.mark.linux_until_port(7)
def test_the_window_manager_leaves_the_handovers_to_the_theme(session):
    """The interface is unmapped while a game runs and mapped again on suspend.
    KWin's defaults zoomed and faded it in for ~150 ms over the theme's own
    handover, and morphed the game window as its fullscreen was dropped."""
    session["run"]()
    kwinrc = (_wm_config_dir(session) / "kwinrc").read_text()

    assert "[Plugins]" in kwinrc
    for effect in ("scale", "glide", "fade", "fullscreen"):
        assert f"{effect}Enabled=false" in kwinrc, f"KWin still animates with {effect}"


@pytest.mark.linux_until_port(7)
def test_openbox_is_given_a_keyboard_with_nothing_on_it(session):
    """The fallback window manager keeps its bindings in its own file, so
    disabling KWin's shortcuts would not have reached it."""
    session["run"]()
    rc = (_wm_config_dir(session) / "openbox" / "rc.xml").read_text()
    assert "<keyboard></keyboard>" in rc
    assert "openbox_config" in rc


@pytest.mark.linux_until_port(7)
def test_openbox_is_told_which_file_to_read(session):
    """By path. Handing openbox a config directory and hoping is how it ends up
    reading the one in the player's home instead."""
    _stub(session["bin"] / "openbox", (session["bin"] / "fake-wm").read_text()
          .replace("#!/usr/bin/env bash\n", ""))
    r = session["run"](pick_its_own=True, candidates="absent-kwin openbox")
    assert r.returncode == 0, r.stderr

    log = _wm_log(session)
    assert "--config-file" in log, log
    assert "/gamecore/wm/openbox/rc.xml" in log, log


def test_the_players_own_configuration_is_never_written_to(session):
    """The same account boots a desktop session on this box. A kiosk that
    rewrote the desktop's key bindings would be a worse bug than Alt+Tab."""
    session["run"]()
    assert not session["player_config"].exists(), (
        "the console session wrote into the player's ~/.config")


@pytest.mark.linux_until_port(7)
def test_the_lockdown_is_reported_so_a_journal_can_show_it(session):
    r = session["run"]()
    assert "key bindings disabled" in r.stdout, r.stdout


# ── the unit's restart bound ────────────────────────────────────────────────

def test_the_restart_limit_is_in_the_section_systemd_reads_it_from():
    """In `[Service]` systemd ignores both keys and says so once per load, then
    runs unbounded — the interface restarting for ever on a box that cannot
    start it, instead of stopping with a reason."""
    unit = UI_UNIT.read_text()
    head, _, service = unit.partition("\n[Service]\n")
    assert "StartLimitIntervalSec=60" in head, "still in [Service], where it is ignored"
    assert "StartLimitBurst=5" in head
    assert "StartLimitIntervalSec" not in service
    assert "StartLimitBurst" not in service


# ── the switch, and being able to ask it anything ───────────────────────────

@pytest.fixture
def switch(tmp_path):
    sddm = tmp_path / "etc" / "sddm.conf.d"
    xses = tmp_path / "usr" / "share" / "xsessions"
    sddm.mkdir(parents=True)
    xses.mkdir(parents=True)
    bins = tmp_path / "bin"
    bins.mkdir()
    _stub(bins / "systemctl", "exit 0")
    _stub(bins / "fake-wm", "exit 0")
    (xses / "plasmax11.desktop").write_text("[Desktop Entry]\nName=Plasma (X11)\n")
    # Arming refuses on a box with no console session installed, and rightly.
    (xses / "gamecore.desktop").write_text("[Desktop Entry]\nName=GameCore\n")

    def run(*args, candidates: str = "fake-wm"):
        return subprocess.run(
            ["bash", str(SELECT), *args],
            env={**os.environ, "SDDM_CONF_DIR": str(sddm), "XSESSIONS_DIR": str(xses),
                 "WAYLAND_SESSIONS_DIR": str(tmp_path / "none"),
                 "GAMECORE_USER": "player", "GAMECORE_USER_HOME": str(tmp_path / "home"),
                 "SYSTEM_UNIT_DIR": str(tmp_path / "units"),
                 "GAMECORE_WM_CANDIDATES": candidates,
                 "GAMECORE_STATE_DIR": str(tmp_path / "state"),
                 "PATH": str(bins) + ":" + os.environ["PATH"]},
            text=True, capture_output=True, timeout=60)

    return {"run": run, "sddm": sddm}


@pytest.mark.linux_until_port(7)
def test_the_auto_login_drop_in_is_readable_without_root(switch):
    """`status` is documented as needing no root, and it is what somebody runs
    when the console will not come up. Under sudo's 077 umask the file it has
    to read came out 0600, and the command answered from the files it could
    still see — which on a Plasma box is `plasma`, whatever the truth was.

    Nothing in it is secret: a user name and a session name, both of which
    `who` and /usr/share/xsessions give away already.
    """
    r = switch["run"]("gamecore")
    assert r.returncode == 0, r.stderr

    conf = switch["sddm"] / "zz-gamecore-autologin.conf"
    assert conf.is_file(), r.stdout
    mode = conf.stat().st_mode & 0o777
    assert mode == 0o644, f"the drop-in is {mode:o}; status cannot read it"
    assert "Session=gamecore" in conf.read_text()


@pytest.mark.linux_until_port(7)
def test_a_file_it_cannot_read_is_said_out_loud_rather_than_skipped(switch):
    """The defect exactly: an unreadable file and a file with no [Autologin]
    in it were the same thing, so the answer was confident and wrong."""
    switch["run"]("gamecore")
    hidden = switch["sddm"] / "zz-zz-someone-elses.conf"
    hidden.write_text("[Autologin]\nSession=plasma\n")
    hidden.chmod(0o000)
    try:
        r = switch["run"]("status")
        # Root can read it regardless, and then there is nothing to warn about.
        if os.geteuid() != 0:
            assert "cannot read" in r.stderr, (r.stdout, r.stderr)
            assert "zz-zz-someone-elses.conf" in r.stderr
    finally:
        hidden.chmod(0o644)


@pytest.mark.linux_until_port(7)
def test_status_still_answers_when_everything_is_readable(switch):
    switch["run"]("gamecore")
    r = switch["run"]("status")
    assert r.returncode == 0, r.stderr
    assert "cannot read" not in r.stderr, r.stderr
    assert "gamecore" in r.stdout


# ── the way back in ─────────────────────────────────────────────────────────

@pytest.mark.linux_until_port(7)
def test_the_launcher_can_arm_the_console_session(tmp_path):
    """A box in desktop mode had no way back that did not involve knowing the
    name of a command. This is the one a player can click."""
    bins = tmp_path / "bin"
    bins.mkdir()
    log = tmp_path / "sudo.log"
    _stub(bins / "systemctl", "exit 0")
    _stub(bins / "sudo", f'echo "$*" >> {log}\nexit 0\n')

    r = subprocess.run(["bash", str(LAUNCHER), "--session"],
                       env={"PATH": f"{bins}:/usr/bin:/bin", "HOME": str(tmp_path)},
                       text=True, capture_output=True, timeout=30)

    asked = log.read_text() if log.exists() else ""
    if "is missing" in r.stdout:
        pytest.skip("no gamecore-session-select on this machine to point at")
    assert "gamecore-session-select gamecore --restart-dm" in asked, asked
    assert r.returncode == 0, r.stdout


@pytest.mark.linux_until_port(7)
def test_the_launcher_refuses_an_argument_it_does_not_know(tmp_path):
    r = subprocess.run(["bash", str(LAUNCHER), "--wat"],
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                       text=True, capture_output=True, timeout=30)
    assert r.returncode == 2
    assert "usage:" in r.stderr


def test_the_launcher_says_which_session_the_box_actually_boots():
    """"GameCore will not start" and "GameCore starts but Alt+Tab leaves it"
    are the same box in the same state, and nothing said so."""
    source = LAUNCHER.read_text()
    assert "boots to the DESKTOP" in source
    assert "--session" in source
    assert "Alt+Tab" in source, "the symptom the owner reports is not named"


@pytest.mark.linux_until_port(7)
def test_the_return_path_is_granted_without_a_password(tmp_path):
    """`--session` runs `sudo -n`. Without the rule it asks for a password that
    a television has no keyboard to type."""
    (tmp_path / "home" / "player").mkdir(parents=True)
    r = subprocess.run(
        ["bash", str(STEP), "player", "/opt/GameCore", "/userdata", "8765"],
        env={**os.environ, "DESTDIR": str(tmp_path), "GAMECORE_USER_HOME": "/home/player"},
        text=True, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr

    rules = (tmp_path / "etc" / "sudoers.d" / "gamecore-session").read_text()
    assert "gamecore-session-select gamecore --restart-dm" in rules, rules
    # And its opposite is still there: leaving must not have been traded away.
    assert "gamecore-session-select desktop --restart-dm" in rules
