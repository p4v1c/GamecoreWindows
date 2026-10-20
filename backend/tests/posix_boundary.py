"""`os.getuid` and `os.geteuid`, for tests of Linux code that still reads them.

The Bluetooth and audio routers (port step 5) and the installer (step 7) ask
for the user id to find a runtime directory or decide on root. Neither exists
on Windows, yet those tests are about something else (parsing, ordering), so
the `posix_boundary` fixture supplies the two names until that code is
ported. The process calls this fixture once played (`os.killpg`, the signals)
left with step 3: games live in Job Objects (`platform/jobs.py`).
"""
from __future__ import annotations

import os

import pytest

TEST_UID = 1000


@pytest.fixture
def posix_boundary(monkeypatch):
    """os.getuid and os.geteuid, on Windows."""
    for name in ("getuid", "geteuid"):
        if not hasattr(os, name):
            monkeypatch.setattr(os, name, lambda: TEST_UID, raising=False)
