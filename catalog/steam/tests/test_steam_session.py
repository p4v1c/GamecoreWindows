"""The Steam session helper: finding steam.exe, closing a Steam started outside
the session, and lasting as long as Steam does (catalog/steam/files/steam_session.py)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
HELPER = PACK / "files" / "steam_session.py"

_spec = importlib.util.spec_from_file_location("steam_session", HELPER)
steam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(steam)


class FakeClock:
    """time.monotonic and time.sleep in one: sleeping advances the clock."""

    def __init__(self):
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, secs: float) -> None:
        self.now += secs


def test_the_registry_path_wins_over_the_default_folder(tmp_path):
    exe = tmp_path / "steam" / "steam.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    # Steam writes forward slashes and lower case.
    found = steam.find_steam(lambda: exe.as_posix().lower(), lambda: tmp_path / "nope.exe")
    assert found is not None and found.resolve() == exe.resolve()


def test_the_default_folder_is_used_when_the_registry_has_nothing(tmp_path):
    exe = tmp_path / "steam.exe"
    exe.write_bytes(b"MZ")
    assert steam.find_steam(lambda: "", lambda: exe) == exe


def test_no_steam_anywhere_is_none(tmp_path):
    assert steam.find_steam(lambda: str(tmp_path / "gone.exe"), lambda: tmp_path / "nope.exe") is None


def test_tasklist_rows_are_parsed_and_the_localised_no_task_line_ignored():
    out = ('"steam.exe","15144","Console","1","48 212 Ko"\n'
           '"steamwebhelper.exe","1384","Console","1","9 000 Ko"\n'
           '"Steam.exe","2000","Console","1","1 Ko"\n')
    assert steam.parse_tasklist(out) == {15144, 2000}
    assert steam.parse_tasklist("INFO : aucune tâche en cours d'exécution ne correspond.\n") == set()


def test_a_steam_already_running_is_asked_to_shut_down_and_waited_for():
    t = FakeClock()
    seen = iter([{1}, {1}, {1}, set()])
    ran = []
    closed = steam.close_running_steam(Path("C:/Steam/steam.exe"), lambda: next(seen),
                                       lambda argv, **kw: ran.append(argv), t.sleep)
    assert closed is True
    assert ran == [[str(Path("C:/Steam/steam.exe")), "-shutdown"]]


def test_no_running_steam_means_nothing_is_shut_down():
    ran = []
    assert steam.close_running_steam(Path("steam.exe"), lambda: set(), lambda *a, **k: ran.append(a))
    assert ran == []


def test_a_steam_that_refuses_to_close_is_reported():
    t = FakeClock()
    assert steam.close_running_steam(Path("steam.exe"), lambda: {1}, lambda *a, **k: None,
                                     t.sleep, t.clock) is False


def test_a_failed_shutdown_command_still_waits_instead_of_crashing():
    t = FakeClock()

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("steam.exe", 60)

    seen = iter([{1}, set()])
    assert steam.close_running_steam(Path("steam.exe"), lambda: next(seen), boom, t.sleep, t.clock) is True


def test_the_session_survives_steam_restarting_itself_after_an_update():
    t = FakeClock()
    # running, gone for 6 s (self-update), back, then gone for good.
    timeline = {range(0, 10): {1}, range(10, 16): set(), range(16, 30): {2}}

    def running():
        return next((pids for span, pids in timeline.items() if int(t.now) in span), set())

    steam.wait_for_steam_to_end(running, t.sleep, t.clock)
    assert t.now >= 30 + steam.RESTART_GRACE_SECS - steam.POLL_SECS


def test_the_helper_without_steam_says_what_to_do(monkeypatch, capsys):
    monkeypatch.setattr(steam, "find_steam", lambda: None)
    assert steam.main(["-gamepadui"]) == 2
    assert "Install Steam" in capsys.readouterr().err


def test_the_helper_script_runs():
    r = subprocess.run([sys.executable, str(HELPER), "--help"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "steam.exe" in r.stdout


def test_the_pack_launches_the_helper_in_big_picture():
    import json
    pack = json.loads((PACK / "pack.json").read_text(encoding="utf-8"))
    assert pack["windows"] is True
    assert pack["launch"]["args"].endswith("steam_session.py' -gamepadui")
    assert "@GAMECORE_PATH@/catalog/steam/files/steam_session.py" in pack["launch"]["args"]


def test_the_helper_starts_steam_with_the_pack_arguments_and_waits_for_it(monkeypatch):
    started = []

    class FakePopen:
        def __init__(self, argv):
            started.append(argv)

        def wait(self):
            return 0

    monkeypatch.setattr(steam, "find_steam", lambda: Path("C:/Steam/steam.exe"))
    monkeypatch.setattr(steam, "close_running_steam", lambda exe: True)
    monkeypatch.setattr(steam, "wait_for_steam_to_end", lambda: started.append("waited"))
    monkeypatch.setattr(steam.subprocess, "Popen", FakePopen)
    assert steam.main(["-gamepadui"]) == 0
    assert started == [[str(Path("C:/Steam/steam.exe")), "-gamepadui"], "waited"]
