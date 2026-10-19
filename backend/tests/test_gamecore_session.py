"""`gamecore-session` — the console session, run for real against stubs.

This is the script SDDM executes instead of logging into a desktop. Everything
it talks to is replaced here: `systemctl` records what it was asked, the
compositor is a shell script that behaves as the test needs, and the runtime
directory is a temporary one. Nothing starts a compositor, a session or a unit
on the machine running this.

What is pinned, and why each one is a failure that would only ever be found on
a television:

  · **it ends when the player leaves, and not before.** A session that exits on
    a compositor crash returns to SDDM, which auto-logs straight back in — a
    login loop with no keyboard to break it.
  · **it hands the environment over rather than letting anything guess it.** The
    user manager gets DISPLAY by import; the backend, which is a system unit
    and cannot see any of that, gets a file written at login and removed at
    logout.
  · **it starts a compositor and nothing else.** No plasmashell, no panel, no
    wallpaper: those are the seconds the owner was watching.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SESSION = REPO / "install" / "bin" / "gamecore-session"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    path.chmod(0o755)


@pytest.fixture
def box(tmp_path):
    """A machine with a systemd, a compositor and nothing else."""
    binds = tmp_path / "bin"
    binds.mkdir()
    calls = tmp_path / "systemctl.log"
    runtime = tmp_path / "run"
    runtime.mkdir()

    # `is-active` answers from a file the test owns, so a scenario can decide
    # when the interface stops.
    (tmp_path / "ui-active").write_text("yes")
    _stub(binds / "systemctl", f"""
        echo "$*" >> {calls}
        for arg in "$@"; do
          if [[ "$arg" == "is-active" ]]; then
            [[ "$(cat {tmp_path}/ui-active)" == "yes" ]] && exit 0 || exit 1
          fi
        done
        exit 0
    """)

    def run(compositor_body: str | None = None, timeout: float = 30,
            pick_its_own: bool = False,
            candidates: str | None = None) -> subprocess.CompletedProcess:
        env = {
            # The stubs come FIRST, and that is not tidiness: without a
            # systemctl of its own the script talks to the real user manager
            # and can start a second interface on the machine running the test.
            "PATH": f"{binds}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "DISPLAY": ":9",
            "XAUTHORITY": str(tmp_path / "cookie"),
            "XDG_RUNTIME_DIR": str(runtime),
        }
        if compositor_body is not None:
            _stub(binds / "fake-compositor", compositor_body)
        if not pick_its_own:
            env["GAMECORE_COMPOSITOR"] = str(binds / "fake-compositor")
        if candidates is not None:
            # What this machine appears to have. Without it a test for "no KWin
            # here" would have to hide a real binary from PATH — and the one
            # that tried launched the developer's actual KWin against a display
            # that does not exist.
            env["GAMECORE_WM_CANDIDATES"] = candidates
        return subprocess.run(["bash", str(SESSION)], env=env, text=True,
                              capture_output=True, timeout=timeout)

    return {"run": run, "calls": calls, "runtime": runtime, "tmp": tmp_path,
            "bin": binds,
            "ui_stops": lambda: (tmp_path / "ui-active").write_text("no")}


def _asked(box) -> str:
    return box["calls"].read_text() if box["calls"].exists() else ""


# ── the environment, handed over rather than guessed ────────────────────────

@pytest.mark.linux_until_port(7)
def test_the_session_writes_its_environment_where_the_backend_can_read_it(box):
    """The backend is a system unit — deliberately, so the API survives without
    a graphical session — and a system unit sees nothing of one. It used to
    probe X for itself: every socket against every cookie location, with a
    twenty-second bound in its unit, on every start."""
    seen = box["runtime"] / "gamecore" / "session.env"
    box["run"]("""
        # Read the file while the session is up, then leave cleanly.
        cp "$XDG_RUNTIME_DIR/gamecore/session.env" "$XDG_RUNTIME_DIR/seen.env"
        exit 0
    """)
    written = (box["runtime"] / "seen.env").read_text()
    assert "DISPLAY=:9" in written
    assert "XAUTHORITY=" in written
    assert not seen.exists(), "the session left its environment behind for the next one"


@pytest.mark.linux_until_port(7)
def test_the_user_manager_is_told_and_then_untold(box):
    box["run"]("exit 0")
    asked = _asked(box)
    assert "import-environment" in asked and "DISPLAY" in asked
    assert "unset-environment" in asked, (
        "a daemon started next would inherit a display that no longer exists")


# ── what it starts ──────────────────────────────────────────────────────────

@pytest.mark.linux_until_port(7)
def test_it_starts_the_session_target_and_stops_it_on_the_way_out(box):
    box["run"]("exit 0")
    asked = _asked(box)
    assert "start gamecore-session.target" in asked
    assert "stop gamecore-session.target" in asked


def _code(path: Path) -> str:
    """The script with its comments stripped — what it DOES, not what it says.

    The prose names the very things the check below forbids, because saying
    what a file deliberately does not do is half of why it is readable.
    """
    return "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())


def test_it_starts_a_compositor_and_nothing_resembling_a_desktop(box):
    """No plasmashell, no panel, no wallpaper, no session restore."""
    box["run"]("exit 0")
    source = _code(SESSION)
    for forbidden in ("plasmashell", "plasma_session", "startplasma", "plasma-workspace"):
        assert forbidden not in source, f"the console session starts {forbidden}"


@pytest.mark.linux_until_port(7)
def test_without_a_display_it_says_so_instead_of_starting(box, tmp_path):
    r = subprocess.run(["bash", str(SESSION)], text=True, capture_output=True, timeout=30,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                            "XDG_RUNTIME_DIR": str(tmp_path)})
    assert r.returncode != 0
    assert "DISPLAY" in r.stdout + r.stderr


# ── what ends it, and what must not ─────────────────────────────────────────

@pytest.mark.linux_until_port(7)
def test_a_compositor_that_exits_cleanly_ends_the_session(box):
    """That is the player leaving — Settings → Desktop closes the session."""
    r = box["run"]("exit 0")
    assert r.returncode == 0
    assert "ending the session" in r.stdout


@pytest.mark.linux_until_port(7)
def test_a_compositor_that_keeps_crashing_does_not_end_the_session(box):
    """The one that would be a login loop on a television.

    SDDM auto-logs in, so a session that exits comes straight back — for ever,
    with no keyboard to break it. The compositor is restarted a bounded number
    of times and then given up on: the interface is fullscreen and still
    visible, the bezels and the stacking are not, and the journal says which.
    """
    r = box["run"](f"""
        echo x >> {box['tmp']}/crashes
        # The interface "stops" once the compositor has been given up on, so
        # this test ends by the OTHER route rather than by a timeout.
        if [[ $(wc -l < {box['tmp']}/crashes) -ge 4 ]]; then
          echo no > {box['tmp']}/ui-active
        fi
        exit 1
    """)
    crashes = (box["tmp"] / "crashes").read_text().splitlines()
    assert len(crashes) == 4, f"restarted {len(crashes)} times, expected 3 retries then a stop"
    assert "carrying on without one" in r.stdout
    assert "ending the session" in r.stdout


@pytest.mark.linux_until_port(7)
def test_the_interface_stopping_ends_the_session(box):
    """Quitting GameCore leaves an X server with nobody drawing on it; the
    session has to go with it."""
    box["ui_stops"]()
    # `exec` frees the pipes this test reads: a background compositor holding
    # them open makes the test wait for the compositor rather than for the
    # session, which is how this test first "failed" against a script that was
    # behaving perfectly.
    r = box["run"]("exec >/dev/null 2>&1; sleep 60", timeout=40)
    assert "no longer running" in r.stdout


# ── the other end: what the backend does with that file ─────────────────────

@pytest.mark.usefixtures("posix_boundary")
def test_the_backend_takes_the_display_from_the_session_file(tmp_path, monkeypatch):
    """The pair that closes the loop: the session writes, the backend reads.

    Without it the backend probes — every X socket against every cookie
    location, on every launch and every standby transition, with a twenty
    second bound in its own unit for the cold-boot case. That probe stays for
    boxes that have not migrated and for sessions that are not ours; it is no
    longer the first answer.
    """
    import sys
    sys.path.insert(0, str(REPO))
    from backend.services import session as xsession

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    # Any probe here would be the failure, not the fallback.
    monkeypatch.setattr(xsession, "_probe_display",
                        lambda uid: pytest.fail("the backend probed X instead of reading the session"))

    session = tmp_path / "gamecore" / "session.env"
    session.parent.mkdir(parents=True)
    session.write_text("DISPLAY=:7\nXAUTHORITY=/tmp/cookie-7\nSTARTED=2026-09-06T21:00:00Z\n")

    env = xsession._display_env()
    assert env["DISPLAY"] == ":7"
    assert env["XAUTHORITY"] == "/tmp/cookie-7"


@pytest.mark.usefixtures("posix_boundary")
def test_no_session_file_means_the_old_behaviour_exactly(tmp_path, monkeypatch):
    """Every box that has not migrated, and every SSH install."""
    import sys
    sys.path.insert(0, str(REPO))
    from backend.services import session as xsession

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    monkeypatch.setattr(xsession, "_probe_cache", None)
    monkeypatch.setattr(xsession, "_probe_retry_at", 0.0)
    probed = []
    monkeypatch.setattr(xsession, "_probe_display", lambda uid: probed.append(uid) or (":3", ""))

    env = xsession._display_env()
    assert probed, "the fallback probe was skipped on a box with no session file"
    assert env["DISPLAY"] == ":3"


# ── what manages the windows ────────────────────────────────────────────────
#
# Recent Plasma ships KWin's X11 binary in its own `kwin-x11` package, so a box
# running Plasma on WAYLAND has `kwin_wayland` and no `kwin_x11` at all. The
# reference box was exactly that, and was one command away from arming a
# console session with no window manager: the interface shows — so it looks
# like it worked — and then the bezels stack at random and a fullscreen
# emulator answers to nobody.

@pytest.mark.linux_until_port(7)
def test_openbox_is_taken_when_there_is_no_kwin_for_x11(box):
    """Not an equivalent, and the code says so: openbox is a window manager,
    not the compositor the overlays were tuned against. The difference that
    matters is against NO window manager at all."""
    _stub(box["bin"] / "openbox", "exit 0")     # exits cleanly: the session ends
    # Two names this machine does not have, then openbox — the shape of a box
    # running Plasma on Wayland, which has kwin_wayland and no kwin_x11 at all.
    # Named rather than hidden from PATH: the real kwin_x11 IS on this
    # developer's machine, and an earlier version of this test launched it.
    r = box["run"](pick_its_own=True, candidates="kwin_x11_absent kwin_absent openbox")
    assert "falling back to openbox" in r.stdout
    assert "pacman -S kwin-x11" in r.stdout, "the way to the intended behaviour is not named"
    assert "starting window manager:" in r.stdout


@pytest.mark.linux_until_port(7)
def test_kwin_is_preferred_over_the_fallback(box):
    _stub(box["bin"] / "fake_kwin", "exit 0")
    _stub(box["bin"] / "openbox", "exit 0")
    r = box["run"](pick_its_own=True, candidates="fake_kwin openbox")
    assert "starting compositor:" in r.stdout
    assert "falling back to openbox" not in r.stdout


@pytest.mark.linux_until_port(7)
def test_no_window_manager_at_all_is_said_out_loud_and_survivable(box):
    """It still starts — a console with no bezels beats no console — but the
    journal has to carry both the reason and the fix."""
    box["ui_stops"]()                            # so the session ends by itself
    r = box["run"](pick_its_own=True, candidates="nothing-here")
    assert "no window manager" in r.stdout
    assert "pacman -S kwin-x11" in r.stdout
    assert "no longer running" in r.stdout, "the session did not end on its own"


def test_the_default_order_prefers_kwin_and_keeps_a_fallback():
    """The list itself, since the tests above are free to override it."""
    line = [ln for ln in SESSION.read_text().splitlines()
            if ln.startswith("WM_CANDIDATES=")]
    assert line and "kwin_x11 kwin openbox" in line[0], line
