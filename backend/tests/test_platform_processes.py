"""Boot identity and process start time, read from this Windows for real.

`process_identity.matches()` decides whether a game left running by a previous
backend is adopted (its PID could belong to anything after a reboot), so these
two values must be stable for one process and differ for another.
"""
from __future__ import annotations

import os
import subprocess
import sys

from backend.services import process_identity
from backend.services.platform import processes

# Odd, and Windows PIDs are multiples of 4: never a real process.
NO_SUCH_PID = 2**31 - 1


def _child() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_the_boot_id_is_a_number_and_stable():
    first = processes.boot_id()
    assert first.isdigit(), first
    assert processes.boot_id() == first


def test_no_boot_id_when_windows_cannot_say(monkeypatch):
    monkeypatch.setattr(processes, "BOOT_ID_KEY", r"SOFTWARE\GameCore\no-such-key")
    assert processes.boot_id() == ""


def test_a_start_time_is_stable_for_one_process_and_differs_for_another():
    child = _child()
    try:
        first = processes.start_time(child.pid)
        assert first and processes.start_time(child.pid) == first
        assert first != processes.start_time(os.getpid())
    finally:
        child.kill()
        child.wait(timeout=10)


def test_no_start_time_for_a_process_that_does_not_exist():
    assert processes.start_time(NO_SUCH_PID) == ""
    assert processes.start_time(-1) == ""


def test_a_saved_identity_matches_only_while_the_process_lives():
    """The adoption check end to end, on real processes."""
    child = _child()
    entry = {"boot_id": process_identity.boot_id(),
             "leader_start": process_identity.start_time(child.pid)}
    try:
        assert process_identity.matches(entry, child.pid)
        assert not process_identity.matches({**entry, "boot_id": "0"}, child.pid)
    finally:
        child.kill()
        child.wait(timeout=10)
    assert not process_identity.matches(entry, child.pid)
