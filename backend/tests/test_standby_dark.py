"""Sleep switches the screen off only once the UI has faded to black.

The screen used to go off in the same breath as `standby:sleep`, so the TV
went from the standby picture to no signal in one frame. The UI now fades,
then posts `/api/standby/dark`; the backend waits for that before the screen goes off.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend import ws
from backend.services import standby


@pytest.fixture
def screen(monkeypatch):
    """Record every screen and governor command instead of running it."""
    calls: list[tuple[str, ...]] = []

    async def fake_power(*argv, **kw):
        calls.append(argv)
        return True

    async def fake_broadcast(event, data=None):
        calls.append(("ws", event))

    monkeypatch.setattr(standby, "_power", fake_power)
    monkeypatch.setattr(ws, "broadcast", fake_broadcast)
    monkeypatch.setattr(ws, "has_clients", lambda: True)
    standby._state = "screensaver"
    yield calls
    standby._state = "active"


def _screen_off(calls) -> bool:
    return ("screen_off",) in calls


def test_screen_stays_on_until_the_ui_is_dark(screen):
    async def scenario():
        entering = asyncio.create_task(standby._enter("sleep"))
        await asyncio.sleep(0.05)
        assert ("ws", "standby:sleep") in screen
        assert not _screen_off(screen)
        standby.ui_dark()
        await entering

    asyncio.run(scenario())
    assert _screen_off(screen)


def test_a_wake_during_the_fade_keeps_the_screen_on(screen):
    async def scenario():
        entering = asyncio.create_task(standby._enter("sleep"))
        await asyncio.sleep(0.05)
        await standby.exit_standby()
        standby.ui_dark()
        await entering

    asyncio.run(scenario())
    assert not _screen_off(screen)
    assert standby.get_state() == "active"


def test_no_ui_answering_still_turns_the_screen_off(screen, monkeypatch):
    monkeypatch.setattr(standby, "_UI_DARK_WAIT_SECS", 0.05)
    asyncio.run(standby._enter("sleep"))
    assert _screen_off(screen)


def test_no_ui_connected_does_not_wait(screen, monkeypatch):
    monkeypatch.setattr(ws, "has_clients", lambda: False)
    monkeypatch.setattr(standby, "_UI_DARK_WAIT_SECS", 60)
    asyncio.run(asyncio.wait_for(standby._enter("sleep"), 1))
    assert _screen_off(screen)


def test_a_stale_dark_from_the_last_sleep_does_not_count(screen):
    standby.ui_dark()          # left over from an earlier fade

    async def scenario():
        entering = asyncio.create_task(standby._enter("sleep"))
        await asyncio.sleep(0.05)
        assert not _screen_off(screen)
        standby.ui_dark()
        await entering

    asyncio.run(scenario())
