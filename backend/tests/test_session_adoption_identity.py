"""A saved pgid is adopted only if it is still the group we started.

session.json lives in config/ and survives a power cut. After a reboot the
kernel hands the same pgid to an unrelated process, and adopting it made the
box say "a game is already running" and SIGKILL the wrong group on double-PS.
"""
import asyncio
import json
import os
import signal
import subprocess
import sys

import pytest

from backend.services import process_identity
from backend.services import process_manager as pm

pytestmark = pytest.mark.usefixtures("posix_boundary")


@pytest.fixture
def session_file(tmp_path, monkeypatch):
    f = tmp_path / "session.json"
    monkeypatch.setattr(pm, "SESSION_FILE", f)
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    return f


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    yield proc
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    proc.wait(timeout=5)


def _entry(pgid: int, **identity) -> dict:
    return {"pgid": pgid, "game_key": "Melee.iso", "system_id": "dolphin",
            "exec_path": "/usr/bin/dolphin-emu", "launch_args": [],
            "rom_path": "", "started_at": 1.0, **identity}


def _adopt(session_file, entry: dict) -> pm.ProcessManager:
    session_file.write_text(json.dumps({"sessions": [entry]}))
    fresh = pm.ProcessManager()
    asyncio.run(fresh.adopt_orphan())
    return fresh


def test_the_group_we_started_is_adopted(session_file, sleeper):
    fresh = _adopt(session_file, _entry(
        sleeper.pid, boot_id=process_identity.boot_id(),
        leader_start=process_identity.start_time(sleeper.pid)))
    assert fresh.is_running


def test_a_pgid_reused_after_a_reboot_is_not_adopted(session_file, sleeper,
                                                     monkeypatch):
    entry = _entry(sleeper.pid, boot_id="boot-before-the-power-cut",
                   leader_start=process_identity.start_time(sleeper.pid))
    fresh = _adopt(session_file, entry)
    assert not fresh.is_running, "a reboot means the saved group is gone"
    assert not session_file.exists()
    assert sleeper.poll() is None, "the unrelated process must be left alone"


def test_a_pgid_reused_by_a_newer_process_is_not_adopted(session_file, sleeper):
    entry = _entry(sleeper.pid, boot_id=process_identity.boot_id(),
                   leader_start="1")
    fresh = _adopt(session_file, entry)
    assert not fresh.is_running
    assert sleeper.poll() is None


def test_a_session_file_without_identity_is_discarded(session_file, sleeper):
    """Written by an older build: there is no way to prove the group is ours."""
    fresh = _adopt(session_file, _entry(sleeper.pid))
    assert not fresh.is_running
    assert not session_file.exists()
    assert sleeper.poll() is None


def test_a_group_we_may_not_signal_is_not_ours(monkeypatch):
    def denied(pgid, sig):
        raise PermissionError
    monkeypatch.setattr(pm.os, "killpg", denied)
    assert not pm._pgid_alive(4242)


def test_the_saved_session_carries_the_identity(session_file, monkeypatch):
    monkeypatch.setattr(process_identity, "boot_id", lambda: "boot-a")
    monkeypatch.setattr(process_identity, "start_time", lambda pid: f"t{pid}")
    m = pm.ProcessManager()
    m._sessions.append(pm.Session(orphan_pgid=4242, game_key="g"))
    m._save_state()
    saved = json.loads(session_file.read_text())["sessions"][0]
    assert saved["boot_id"] == "boot-a"
    assert saved["leader_start"] == "t4242"

