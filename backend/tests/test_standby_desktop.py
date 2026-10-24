"""Desktop mode: Windows decides while the player is on its desktop.

The shell posts `/api/standby/desktop` when it leaves for Explorer and again
when it comes back. Without it the watcher re-claimed Windows' idle timers every
minute, so a desktop left alone never turned its screen off, and GameCore's
screensaver could start over somebody's desktop.
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from backend.services import desktop_power, standby

CFG_ON = {"enabled": True, "screensaver_mins": 10, "sleep_mins": 20}


@pytest.fixture
def timers(monkeypatch):
    calls = []

    async def claim():
        calls.append("claim")
        return True

    async def release():
        calls.append("release")
        return True

    monkeypatch.setattr(desktop_power, "claim", claim)
    monkeypatch.setattr(desktop_power, "release", release)
    monkeypatch.setattr(standby, "load_config", lambda: dict(CFG_ON))
    monkeypatch.setattr(standby, "_last_claim_attempt", 0.0)
    entered = []

    async def enter(stage):
        entered.append(stage)
    monkeypatch.setattr(standby, "_enter", enter)
    yield calls, entered
    standby._on_desktop = False


def test_leaving_for_the_desktop_hands_the_timers_back(timers):
    calls, _ = timers
    asyncio.run(standby.set_desktop(True))
    assert calls == ["release"]


def test_on_the_desktop_the_watcher_neither_reclaims_nor_blanks(timers):
    calls, entered = timers
    asyncio.run(standby.set_desktop(True))
    standby._last_input = time.monotonic() - 3600
    asyncio.run(standby._tick(CFG_ON))
    assert calls == ["release"]
    assert entered == []


def test_coming_back_takes_the_timers_and_restarts_the_idle_clock(timers):
    calls, entered = timers
    asyncio.run(standby.set_desktop(True))
    standby._last_input = time.monotonic() - 3600
    asyncio.run(standby.set_desktop(False))
    asyncio.run(standby._tick(CFG_ON))
    assert calls[:2] == ["release", "claim"]
    assert entered == []


def test_coming_back_with_standby_off_leaves_windows_in_charge(timers, monkeypatch):
    calls, _ = timers
    monkeypatch.setattr(standby, "load_config", lambda: {**CFG_ON, "enabled": False})
    asyncio.run(standby.set_desktop(False))
    assert calls == []


def test_the_route_reaches_the_service(timers):
    from backend.main import app
    calls, _ = timers
    with TestClient(app) as client:
        calls.clear()
        assert client.post("/api/standby/desktop", json={"active": True}).json() == {"ok": True}
        assert client.post("/api/standby/desktop", json={}).status_code == 422
    assert calls[0] == "release"
