"""`gamecore-shell` and `kiosk_desktop`: the console user's shell, and the desktop round trip it runs.

Driven through `kiosk_desktop.Shell` with a dict registry and fake effects: nothing here
starts Explorer, runs a task or writes the real HKCU. What is pinned, each a
failure only a reboot would show:

  · the session task is started at once, and again if the session dies, never in a loop;
  · Explorer starts as the desktop (Shell value pointed at it) and the GameCore
    shell is back in the profile before the trip ends, even when Explorer fails;
  · a trip cut short is undone by the next session (rearm).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "install" / "bin"
SCRIPT = BIN / "gamecore-shell"
GAMECORE_SHELL = r'"C:\GameCore\.venv\Scripts\pythonw.exe" "C:\GameCore\install\bin\gamecore-shell"'

sys.path.insert(0, str(BIN))
import kiosk_desktop as kd  # noqa: E402
import kiosk_shell as ks  # noqa: E402


def _load():
    loader = importlib.machinery.SourceFileLoader("gamecore_shell", str(SCRIPT))
    spec = importlib.util.spec_from_loader("gamecore_shell", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


shell_module = _load()


class DictRegistry:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []

    def get(self, key, name):
        return self.values.get((key, name))

    def set(self, key, name, value):
        self.writes.append((name, value))
        self.values[(key, name)] = value

    def delete(self, key, name):
        self.values.pop((key, name), None)


class World:
    """The session, the taskbar and Explorer, as the shell sees them."""

    def __init__(self, registry):
        self.registry = registry
        self.now = 0.0
        self.session = False
        self.taskbar = 0
        self.runs = []
        self.explorer_starts = []
        self.exits = []
        self.explorer_comes_up = True
        self.explorer_leaves = True

    def shell(self):
        return kd.Shell(
            self.registry, run=self._run, session_running=lambda: self.session,
            taskbar=lambda: self.taskbar, explorer=self._explorer, exit_explorer=self._exit,
            clock=lambda: self.now, sleep=self._sleep)

    def _run(self, argv):
        self.runs.append(argv)
        return 0

    def _explorer(self):
        # Explorer reads the Shell value as it starts: a desktop only if it names Explorer.
        self.explorer_starts.append(self.registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE))
        if self.explorer_comes_up:
            self.taskbar = 0x1234

    def _exit(self, window):
        self.exits.append(window)
        if self.explorer_leaves:
            self.taskbar = 0
        return True

    def _sleep(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def no_real_desktop(monkeypatch):
    monkeypatch.setattr(kd, "black_desktop", lambda: None)
    monkeypatch.setattr(kd, "restore_wallpaper", lambda registry: None)


@pytest.fixture
def world():
    return World(DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): GAMECORE_SHELL}))


def test_the_shell_starts_the_session_task_at_once(world):
    world.shell().keep_session()
    assert world.runs == [["schtasks.exe", "/run", "/tn", r"\GameCore\Session"]]


def test_a_running_session_is_not_started_again(world):
    world.session = True
    world.shell().keep_session()
    assert world.runs == []


def test_a_session_that_died_is_started_again_at_a_bounded_pace(world):
    shell = world.shell()
    shell.keep_session()
    world.now += kd.SESSION_RETRY_S - 1
    shell.keep_session()
    assert len(world.runs) == 1
    world.now += 1
    shell.keep_session()
    assert len(world.runs) == 2


def test_desktop_mode_starts_explorer_as_the_desktop_and_puts_gamecore_back(world):
    assert world.shell().open_desktop() is True
    assert world.explorer_starts == [ks.EXPLORER]
    assert world.registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == GAMECORE_SHELL
    assert world.registry.get(ks.GAMECORE_KEY, ks.REARM_VALUE) is None


def test_explorer_that_never_shows_a_taskbar_still_gives_the_shell_back(world):
    world.explorer_comes_up = False
    assert world.shell().open_desktop() is False
    assert world.registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == GAMECORE_SHELL


def test_explorer_that_fails_to_start_still_gives_the_shell_back(world):
    def broken():
        raise OSError("explorer.exe not found")
    shell = world.shell()
    shell._explorer = broken
    with pytest.raises(OSError):
        shell.open_desktop()
    assert world.registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == GAMECORE_SHELL


def test_a_desktop_already_open_starts_no_second_explorer(world):
    world.taskbar = 0x1234
    assert world.shell().open_desktop() is True
    assert world.explorer_starts == [] and world.registry.writes == []


def test_where_explorer_is_still_the_shell_the_profile_is_not_touched():
    world = World(DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): "explorer.exe"}))
    world.shell().open_desktop()
    assert world.registry.writes == []


def test_coming_back_asks_explorer_to_exit(world):
    world.taskbar = 0x1234
    assert world.shell().close_desktop() is True
    assert world.exits == [0x1234] and world.runs == []


def test_an_explorer_that_will_not_exit_is_ended(world, monkeypatch):
    monkeypatch.setattr(kd, "session_id", lambda: 1)
    world.taskbar = 0x1234
    world.explorer_leaves = False
    assert world.shell().close_desktop() is False
    assert world.runs == [["taskkill.exe", "/f", "/im", "explorer.exe", "/fi", "SESSION eq 1"]]


def test_a_trip_cut_short_is_undone_by_the_next_session():
    registry = DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): GAMECORE_SHELL})
    ks.point_shell_at_explorer(registry)              # the shell died here
    assert registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == ks.EXPLORER
    assert ks.rearm(registry) is True
    assert registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == GAMECORE_SHELL
    assert ks.rearm(registry) is False                # nothing left to undo


def test_the_gamecore_shell_is_recognised_even_mid_trip():
    assert ks.is_gamecore_shell(DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): GAMECORE_SHELL}))
    mid_trip = DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): "explorer.exe",
                             (ks.GAMECORE_KEY, ks.REARM_VALUE): GAMECORE_SHELL})
    assert ks.is_gamecore_shell(mid_trip)
    assert not ks.is_gamecore_shell(DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): "explorer.exe"}))
    assert not ks.is_gamecore_shell(DictRegistry())


def test_wait_until_gives_up_after_its_timeout():
    slept = []
    assert ks.wait_until(lambda: False, 1.0, slept.append, step_s=0.25) is False
    assert sum(slept) == 1.0


@pytest.mark.skipif(sys.platform != "win32", reason="named kernel objects are a Windows mechanism")
def test_signalling_an_event_nobody_created_says_so():
    assert ks.signal_event(r"Local\GameCoreTestNobody-" + str(id(REPO))) is False


def test_the_help_runs_on_the_installed_layout():
    out = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "--desktop" in out.stdout
