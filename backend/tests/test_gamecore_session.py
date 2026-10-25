"""`gamecore-session`: the console session the logon task starts.

Driven through its Supervisor with a fake spawn and a fake clock: nothing here
starts a backend or an interface. What is pinned, each a failure only a TV
would show:

  · the backend gets UTF-8 mode and both roots, and a console nobody sees;
  · the interface gets GAMECORE_MANAGED=1, or it starts a second backend;
  · a crash restarts, a player leaving for the desktop does not.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "install" / "bin" / "gamecore-session"
sys.path.insert(0, str(SCRIPT.parent))     # kiosk_shell, beside the script


def _load():
    loader = importlib.machinery.SourceFileLoader("gamecore_session", str(SCRIPT))
    spec = importlib.util.spec_from_loader("gamecore_session", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gamecore_session"] = module    # dataclasses look the module up
    loader.exec_module(module)
    return module


gs = _load()


class FakeProc:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def settings(tmp_path):
    env_file = tmp_path / "session.env"
    env_file.write_text("# written by the installer\n"
                        f"GAMECORE_PATH={tmp_path / 'code'}\n"
                        f"GAMECORE_DATA={tmp_path / 'data'}\n"
                        "GAMECORE_BACKEND_PORT=8765\n"
                        "THEGAMESDB_API_KEY=k=with=equals\n", encoding="utf-8")
    return gs.load_settings(env_file, {"PATH": "C:\\Windows"})


@pytest.fixture
def started(settings):
    spawned = []

    def spawn(argv, env, cwd, log_path, hidden):
        proc = FakeProc()
        spawned.append({"argv": argv, "env": env, "cwd": cwd, "log": log_path,
                        "hidden": hidden, "proc": proc})
        return proc

    clock = Clock()
    sup = gs.Supervisor(settings, spawn, clock=clock)
    sup.start()
    return sup, spawned, clock


def test_the_env_file_is_read_raw_with_comments_skipped():
    values = gs.parse_env_file("# c\n\nA=1\nB = two words \nC=x=y\nnot a line\n")
    assert values == {"A": "1", "B": "two words", "C": "x=y"}


def test_a_bom_from_notepad_does_not_hide_the_first_key(tmp_path):
    env_file = tmp_path / "session.env"
    env_file.write_bytes(b"\xef\xbb\xbfGAMECORE_PATH=C:\\gc\nGAMECORE_DATA=D:\\data\n")
    assert gs.load_settings(env_file, {}).root == Path("C:\\gc")


def test_a_missing_env_file_says_how_to_get_one(tmp_path):
    with pytest.raises(gs.SessionError, match="install.ps1"):
        gs.load_settings(tmp_path / "absent.env", {})


def test_an_env_file_without_the_roots_is_refused(tmp_path):
    env_file = tmp_path / "session.env"
    env_file.write_text("GAMECORE_PATH=C:\\gc\n", encoding="utf-8")
    with pytest.raises(gs.SessionError, match="GAMECORE_DATA"):
        gs.load_settings(env_file, {})


def test_the_backend_runs_the_venv_uvicorn_on_loopback_with_a_hidden_console(started, settings):
    _, spawned, _ = started
    backend = spawned[0]
    assert backend["argv"][0] == str(settings.root / ".venv" / "Scripts" / "python.exe")
    assert backend["argv"][1:] == ["-m", "uvicorn", "backend.main:app",
                                   "--host", "127.0.0.1", "--port", "8765"]
    assert backend["hidden"] is True
    assert backend["cwd"] == settings.root
    assert backend["log"] == settings.data / "logs" / "session" / "backend-console.log"


def test_the_backend_gets_utf8_mode_the_roots_and_the_secrets(started, settings):
    env = started[1][0]["env"]
    assert env["PYTHONUTF8"] == "1"
    assert env["GAMECORE_PATH"] == str(settings.root)
    assert env["GAMECORE_DATA"] == str(settings.data)
    assert env["THEGAMESDB_API_KEY"] == "k=with=equals"
    assert env["PATH"] == "C:\\Windows"
    assert "GAMECORE_MANAGED" not in env


def test_the_interface_is_told_its_backend_is_managed(started, settings):
    interface = started[1][1]
    assert interface["argv"] == [str(settings.root / "shell" / "GameCore.exe")]
    assert interface["env"]["GAMECORE_MANAGED"] == "1"
    assert interface["hidden"] is False


def test_a_backend_crash_restarts_after_the_delay(started):
    sup, spawned, clock = started
    spawned[0]["proc"].code = 1
    sup.tick()
    assert len(spawned) == 2
    clock.now += gs.RESTART_DELAY_S - 0.1
    sup.tick()
    assert len(spawned) == 2
    clock.now += 0.2
    sup.tick()
    assert len(spawned) == 3 and spawned[2]["argv"] == spawned[0]["argv"]


def test_a_backend_that_keeps_crashing_is_retried_slower_not_abandoned(started):
    sup, spawned, clock = started
    for _ in range(gs.CRASH_BURST):
        current = [s for s in spawned if s["argv"] == spawned[0]["argv"]][-1]
        current["proc"].code = 1
        sup.tick()
        clock.now += gs.RESTART_DELAY_S
        sup.tick()
    backends = [s for s in spawned if s["argv"] == spawned[0]["argv"]]
    assert len(backends) == gs.CRASH_BURST       # the last crash waits the slow delay
    clock.now += gs.SLOW_RESTART_DELAY_S
    sup.tick()
    assert len([s for s in spawned if s["argv"] == spawned[0]["argv"]]) == gs.CRASH_BURST + 1


def test_a_backend_that_exits_cleanly_is_restarted_too(started):
    sup, spawned, clock = started
    spawned[0]["proc"].code = 0
    sup.tick()
    clock.now += gs.RESTART_DELAY_S
    sup.tick()
    assert len(spawned) == 3


def test_an_interface_crash_restarts_it(started):
    sup, spawned, clock = started
    spawned[1]["proc"].code = 3221225477      # 0xC0000005, an access violation
    sup.tick()
    clock.now += gs.RESTART_DELAY_S
    sup.tick()
    assert len(spawned) == 3 and spawned[2]["argv"] == spawned[1]["argv"]


def test_leaving_for_the_desktop_is_not_undone(started):
    sup, spawned, clock = started
    spawned[1]["proc"].code = 0
    sup.tick()
    clock.now += 3600
    sup.tick()
    assert len(spawned) == 2


def test_coming_back_from_the_desktop_starts_the_interface(started):
    sup, spawned, _ = started
    spawned[1]["proc"].code = 0
    sup.tick()
    sup.show_interface()
    assert len(spawned) == 3 and spawned[2]["env"]["GAMECORE_MANAGED"] == "1"


def test_desktop_mode_and_the_way_back_reach_the_shell(settings):
    events = []
    spawned = []

    def spawn(argv, env, cwd, log_path, hidden):
        spawned.append(FakeProc())
        return spawned[-1]

    sup = gs.Supervisor(settings, spawn, clock=Clock(), on_desktop=lambda: events.append("desktop"),
                        on_return=lambda: events.append("return"))
    sup.start()
    spawned[1].code = 0
    sup.tick()
    sup.show_interface()
    assert events == ["desktop", "return"]


def test_an_interface_crash_is_not_a_trip_to_the_desktop(settings):
    events = []
    spawned = []

    def spawn(argv, env, cwd, log_path, hidden):
        spawned.append(FakeProc())
        return spawned[-1]

    sup = gs.Supervisor(settings, spawn, clock=Clock(), on_desktop=lambda: events.append("desktop"))
    sup.start()
    spawned[1].code = 1
    sup.tick()
    assert events == []


class _FakeTrip:
    def __init__(self, calls):
        self.calls = calls

    def __call__(self, registry):
        return self

    def open(self):
        self.calls.append("open")

    def close(self):
        self.calls.append("close")


@pytest.fixture
def trips(monkeypatch):
    calls = []
    monkeypatch.setattr(gs.kiosk_shell, "WinRegistry", lambda: "registry")
    monkeypatch.setattr(gs.kiosk_desktop, "DesktopTrip", _FakeTrip(calls))
    monkeypatch.setattr(gs, "_in_background", lambda action: action())
    return calls


def test_desktop_mode_and_the_way_back_are_the_elevated_sessions_trip(trips):
    gs.leave_to_desktop()
    gs.back_from_desktop()
    assert trips == ["open", "close"]


def test_trips_run_one_at_a_time(monkeypatch):
    import threading
    order = []
    release = threading.Event()

    def slow():
        order.append("open-start")
        release.wait(5)
        order.append("open-end")

    gs._in_background(slow)
    gs._in_background(lambda: order.append("close"))
    release.set()
    for _ in range(200):
        if len(order) == 3:
            break
        threading.Event().wait(0.01)
    assert order == ["open-start", "open-end", "close"]


@pytest.mark.skipif(sys.platform != "win32", reason="GetTickCount64 and WTS are Windows calls")
def test_the_startup_timeline_names_boot_and_sign_in():
    assert gs.startup_timeline().startswith("boot +")


def test_asking_to_show_a_running_interface_starts_nothing(started):
    sup, spawned, _ = started
    sup.show_interface()
    assert len(spawned) == 2


def test_a_program_that_cannot_start_is_retried(settings):
    attempts = []
    clock = Clock()

    def spawn(argv, env, cwd, log_path, hidden):
        attempts.append(argv)
        if len(attempts) == 1:
            raise FileNotFoundError(2, "not found")
        return FakeProc()

    sup = gs.Supervisor(settings, spawn, clock=clock)
    sup.start()
    clock.now += gs.RESTART_DELAY_S
    sup.tick()
    assert attempts[0] == attempts[2] == settings.backend_argv()


@pytest.mark.skipif(sys.platform != "win32", reason="CREATE_NO_WINDOW is a Windows flag")
def test_a_hidden_child_has_a_console_without_a_window(tmp_path):
    """Its own console programs inherit it, so none flashes a window over the TV."""
    probe = ("import ctypes; k = ctypes.windll.kernel32; "
             "print(bool(k.GetConsoleCP()), bool(k.GetConsoleWindow()))")
    proc = gs.spawn_process([sys.executable, "-c", probe], dict(os.environ), tmp_path,
                            tmp_path / "logs" / "out.log", hidden=True)
    assert proc.wait(timeout=30) == 0
    assert (tmp_path / "logs" / "out.log").read_text().split() == ["True", "False"]


def test_the_check_flag_prints_the_settings_without_starting_anything(tmp_path):
    env_file = tmp_path / "session.env"
    env_file.write_text(f"GAMECORE_PATH={tmp_path}\nGAMECORE_DATA={tmp_path / 'd'}\n",
                        encoding="utf-8")
    out = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True,
                         text=True, env={**os.environ, gs.ENV_FILE_OVERRIDE: str(env_file)},
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert "GAMECORE_MANAGED" not in out.stdout
    assert str(tmp_path / "shell" / "GameCore.exe") in out.stdout
    assert not (tmp_path / "d").exists()


def test_without_its_env_file_it_exits_and_leaves_a_note(tmp_path):
    env_file = tmp_path / "GameCore" / "session.env"
    env_file.parent.mkdir()
    out = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                         env={**os.environ, gs.ENV_FILE_OVERRIDE: str(env_file),
                              gs.INSTANCE_OVERRIDE: f"-test-{os.getpid()}"}, timeout=60)
    assert out.returncode == 2
    assert "install.ps1" in (env_file.parent / "session-error.log").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="named kernel objects are a Windows mechanism")
def test_a_second_copy_finds_the_first_and_wakes_it(tmp_path):
    names = {"mutex_name": rf"Local\GameCoreTest-{os.getpid()}-{tmp_path.name}",
             "event_name": rf"Local\GameCoreTestShow-{os.getpid()}-{tmp_path.name}"}
    first, second = gs.SingleInstance(**names), gs.SingleInstance(**names)
    assert first.acquire() is True
    assert first.show_requested(0) is False
    assert second.acquire() is False
    assert second.ask_first_copy_to_show() is True
    assert first.show_requested(0) is True
    assert first.show_requested(0) is False       # auto-reset: one press, one show


@pytest.mark.skipif(sys.platform != "win32", reason="named kernel objects are a Windows mechanism")
def test_the_shortcut_wakes_the_running_copy_without_reading_session_env(tmp_path, monkeypatch):
    # The shortcut runs unelevated: session.env (admin-only) must not stop it waking the session.
    names = {"mutex_name": rf"Local\GameCoreTest-{os.getpid()}-{tmp_path.name}",
             "event_name": rf"Local\GameCoreTestShow-{os.getpid()}-{tmp_path.name}"}
    first = gs.SingleInstance(**names)
    assert first.acquire() is True
    real = gs.SingleInstance
    monkeypatch.setattr(gs, "SingleInstance", lambda: real(**names))
    monkeypatch.setenv(gs.ENV_FILE_OVERRIDE, str(tmp_path / "missing" / "session.env"))
    assert gs.main([]) == 0
    assert first.show_requested(0) is True
