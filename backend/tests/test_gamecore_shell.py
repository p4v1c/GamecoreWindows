"""`gamecore-shell` and `kiosk_desktop`: the console's shell, and the desktop round trip.

Driven through `kiosk_desktop.Shell` and `DesktopTrip` with a dict registry
and fake effects: nothing here starts Explorer, runs a task or writes the real
registry. What is pinned, each a failure only a reboot would show:

  · the session task is started at once, and again if the session dies, never in a loop;
  · Explorer starts while HKLM `Shell` names it (or it is a folder window, no
    taskbar) and `Shell` is GameCore's again before the trip ends, even when
    Explorer never shows a taskbar or fails to start;
  · a shell value changed by anything else is put back at the next session start.
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


def kiosk_registry(shell=GAMECORE_SHELL):
    return DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): shell,
                         (ks.GAMECORE_KEY, ks.KIOSK_VALUE): GAMECORE_SHELL})


class World:
    """The session, the taskbar and Explorer, as both sides see them."""

    def __init__(self, registry):
        self.registry = registry
        self.now = 0.0
        self.session = False
        self.taskbar = 0
        self.runs = []
        self.started = []
        self.explorer_saw = []
        self.exits = []
        self.shell_listens = True
        self.explorer_comes_up = True
        self.explorer_leaves = True
        self.ending = False

    def shell(self):
        return kd.Shell(DictRegistry(), run=self._run, session_running=lambda: self.session,
                        start=self._start, ending=lambda: self.ending, clock=lambda: self.now)

    def trip(self):
        return kd.DesktopTrip(self.registry, run=self._run, ask_shell=self._ask_shell, start=self._start,
                              taskbar=lambda: self.taskbar, exit_explorer=self._exit,
                              ending=lambda: self.ending, sleep=self._sleep)

    def _ask_shell(self):
        if self.shell_listens:
            self._start([ks.EXPLORER])      # what gamecore-shell does on the event
        return self.shell_listens

    def _run(self, argv):
        self.runs.append(argv)
        return 0

    def _start(self, argv):
        self.started.append(argv[0])
        if argv[0] == ks.EXPLORER:
            # Explorer reads HKLM Shell as it starts: the desktop only if it names Explorer.
            self.explorer_saw.append(self.registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE))
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
    monkeypatch.setattr(kd, "session_id", lambda: 1)


@pytest.fixture
def world():
    return World(kiosk_registry())


# -- the shell's side -----------------------------------------------------------

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


def test_no_session_is_started_while_windows_signs_out(world):
    world.ending = True
    world.shell().keep_session()
    assert world.runs == []


# -- the session's side ---------------------------------------------------------

def test_desktop_mode_starts_explorer_while_the_shell_names_it_then_puts_gamecore_back(world):
    assert world.trip().open() is True
    assert world.explorer_saw == [ks.EXPLORER]
    assert world.registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == GAMECORE_SHELL


def test_without_gamecore_shell_the_session_starts_explorer_itself(world):
    world.shell_listens = False
    assert world.trip().open() is True
    assert world.started == [ks.EXPLORER] and world.explorer_saw == [ks.EXPLORER]


def test_explorer_without_a_taskbar_gives_the_shell_back_and_opens_task_manager(world):
    world.explorer_comes_up = False
    assert world.trip().open() is False
    assert world.registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == GAMECORE_SHELL
    assert world.started[-1] == "taskmgr.exe"


def test_explorer_that_fails_to_start_still_gives_the_shell_back(world):
    world.shell_listens = False
    trip = world.trip()
    trip._start = lambda argv: (_ for _ in ()).throw(OSError("explorer.exe not found"))
    with pytest.raises(OSError):
        trip.open()
    assert world.registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == GAMECORE_SHELL


def test_a_desktop_already_open_starts_no_second_explorer(world):
    world.taskbar = 0x1234
    assert world.trip().open() is True
    assert world.started == [] and world.registry.writes == []


def test_a_sign_out_is_not_a_trip_to_the_desktop(world):
    """The interface exits 0 when Windows signs out too."""
    world.ending = True
    assert world.trip().open() is False
    assert world.started == [] and world.registry.writes == []


def test_with_explorer_as_the_real_shell_the_value_is_never_touched():
    world = World(DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): "explorer.exe"}))
    world.shell_listens = False
    world.trip().open()
    assert world.registry.writes == []


def test_coming_back_asks_explorer_to_exit_then_ends_every_leftover_by_name(world):
    world.taskbar = 0x1234
    assert world.trip().close() is True
    assert world.exits == [0x1234]
    assert world.runs == [["taskkill.exe", "/f", "/im", "explorer.exe", "/fi", "SESSION eq 1"]]


def test_an_explorer_that_will_not_exit_is_reported_forced(world):
    world.taskbar = 0x1234
    world.explorer_leaves = False
    assert world.trip().close() is False
    assert world.runs[-1][:4] == ["taskkill.exe", "/f", "/im", "explorer.exe"]


def test_with_explorer_as_the_real_shell_coming_back_leaves_it_running():
    world = World(DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): "explorer.exe"}))
    world.taskbar = 0x1234
    world.trip().close()
    assert world.exits == [] and world.runs == []


# -- re-arming ------------------------------------------------------------------

def test_a_shell_reset_to_explorer_is_put_back_at_session_start():
    registry = kiosk_registry(shell="explorer.exe")
    assert ks.rearm(registry) is True
    assert registry.get(ks.WINLOGON_KEY, ks.SHELL_VALUE) == GAMECORE_SHELL
    assert ks.rearm(registry) is False


def test_without_kiosk_the_shell_is_never_rearmed():
    registry = DictRegistry({(ks.WINLOGON_KEY, ks.SHELL_VALUE): "explorer.exe"})
    assert ks.rearm(registry) is False and registry.writes == []


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
