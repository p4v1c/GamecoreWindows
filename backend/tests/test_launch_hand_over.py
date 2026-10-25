"""The hand-over after a launch: the window to the front, then `game:presented`.

The interface stays shown, black and locked, until that event; it must come
whether or not a window ever showed up. Window calls are faked.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.services import launch

STARTED = SimpleNamespace(game_key="steam", system_id="steam", session_id=4, job=object())


@pytest.fixture
def sent(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(launch.ws, "broadcast",
                        AsyncMock(side_effect=lambda ev, data: events.append((ev, data))))
    return events


def test_the_window_is_brought_forward_before_the_interface_is_told(monkeypatch, sent):
    order = []

    async def present(system_id, job):
        order.append("front")
        return True

    monkeypatch.setattr(launch.window_focus, "present", present)
    asyncio.run(launch._hand_over({"id": "steam"}, "steam", STARTED))
    assert order == ["front"]
    assert sent == [("game:presented", {"game_key": "steam", "system_id": "steam",
                                        "session": 4, "window": True})]


def test_no_window_at_the_timeout_still_lets_the_interface_go(monkeypatch, sent):
    monkeypatch.setattr(launch.window_focus, "present", AsyncMock(return_value=False))
    asyncio.run(launch._hand_over({"id": "steam"}, "steam", STARTED))
    assert sent[0][0] == "game:presented" and sent[0][1]["window"] is False


def test_a_fullscreen_tile_is_presented_once_the_enforcer_is_done(monkeypatch, sent):
    monkeypatch.setattr(launch.fullscreen_enforcer, "enforce", AsyncMock(return_value=True))
    monkeypatch.setattr(launch.window_focus, "present", AsyncMock(side_effect=AssertionError))
    asyncio.run(launch._hand_over({"id": "x", "fullscreen": {"timeout_s": 5}}, "x", STARTED))
    assert sent[0][1]["window"] is True
