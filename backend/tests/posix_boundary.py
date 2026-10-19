"""The POSIX process calls, played by psutil, for tests of Linux process code.

`process_manager`, `rest_mode`, `session` and the gesture code signal process
groups (`os.killpg`, `SIGSTOP`, `SIGCONT`, `SIGKILL`) and read `os.getuid`.
None of those exist on Windows, yet most of their tests are about bookkeeping:
which session is suspended, what is refused, what is broadcast. The
`posix_boundary` fixture supplies the missing names so that logic runs on
Windows until port step 3 replaces the mechanism (Job Objects); a test that
records the calls still patches its own fakes over these.

A "group" here is a process and its descendants, which is what a game's
process group is on Linux.
"""
from __future__ import annotations

import os
import signal

import psutil
import pytest

# Linux numbers, so a test comparing with signal.SIGSTOP reads the same value.
LINUX_SIGNALS = {"SIGKILL": 9, "SIGSTOP": 19, "SIGCONT": 18}
TEST_UID = 1000


def _tree(pgid: int) -> list[psutil.Process]:
    try:
        leader = psutil.Process(pgid)
        return [leader, *leader.children(recursive=True)]
    except (psutil.NoSuchProcess, ValueError):
        raise ProcessLookupError(pgid) from None


def _killpg(pgid: int, sig: int) -> None:
    group = _tree(pgid)
    for proc in group:
        try:
            if sig == LINUX_SIGNALS["SIGKILL"]:
                proc.kill()
            elif sig == LINUX_SIGNALS["SIGSTOP"]:
                proc.suspend()
            elif sig == LINUX_SIGNALS["SIGCONT"]:
                proc.resume()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            raise PermissionError(pgid) from None


def _getpgid(pid: int) -> int:
    if pid == 0:
        return os.getpid()
    _tree(pid)
    return pid


@pytest.fixture
def posix_boundary(monkeypatch):
    """os.killpg, os.getpgid, os.getuid and the three signals, on Windows."""
    for name, number in LINUX_SIGNALS.items():
        if not hasattr(signal, name):
            monkeypatch.setattr(signal, name, number, raising=False)
    for name, fake in (("killpg", _killpg), ("getpgid", _getpgid),
                       ("getuid", lambda: TEST_UID), ("geteuid", lambda: TEST_UID)):
        if not hasattr(os, name):
            monkeypatch.setattr(os, name, fake, raising=False)
