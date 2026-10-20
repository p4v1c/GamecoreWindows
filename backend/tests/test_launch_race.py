"""Two launches in quick succession: the second is refused before it touches disk.

The foreground check used to repeat only at spawn. The second request ran
`_prepare` (profile saves, per-game config, pack prepare) over the game being
started, then, refused at spawn, released the profile saves with `started=now`,
which passed the `_placed_at > started` guard and reset the running game's saves.
"""
import asyncio

import pytest

from backend.services import launch as launch_service
from backend.services import process_manager as pm

SYSTEM = {"id": "dolphin", "kind": "emulator", "path": "/usr/bin/dolphin-emu",
          "args": ""}


@pytest.fixture
def staged(monkeypatch):
    """A launch pipeline whose disk and spawn steps only record their calls."""
    calls = {"prepare": 0, "spawn": 0, "release": 0}
    inside_prepare = asyncio.Event()
    finish_prepare = asyncio.Event()

    async def gates(system, system_id, exec_args, game_key):
        return exec_args

    async def prepare(*_args):
        calls["prepare"] += 1
        if calls["prepare"] == 1:     # hold the first launch, never a second
            inside_prepare.set()
            await finish_prepare.wait()

    async def pack_command(system_id, rom_path, exec_path, exec_args):
        return exec_path, exec_args

    async def spawn(*_args):
        calls["spawn"] += 1
        return False

    monkeypatch.setattr(launch_service, "process_manager", pm.ProcessManager())
    monkeypatch.setattr(launch_service, "_gates", gates)
    monkeypatch.setattr(launch_service, "_prepare", prepare)
    monkeypatch.setattr(launch_service, "_pack_launch_command", pack_command)
    monkeypatch.setattr(launch_service, "_spawn", spawn)
    monkeypatch.setattr(launch_service, "start_window_task", lambda *a: None)
    monkeypatch.setattr(launch_service, "_release_profile_saves",
                        lambda system_id: calls.__setitem__("release",
                                                            calls["release"] + 1))
    return calls, inside_prepare, finish_prepare


def test_a_second_launch_during_the_first_is_refused_before_prepare(staged):
    calls, inside_prepare, finish_prepare = staged

    async def scenario():
        first = asyncio.create_task(
            launch_service.launch(SYSTEM, "dolphin", "", "a.iso"))
        await inside_prepare.wait()
        try:
            await launch_service.launch(SYSTEM, "dolphin", "", "b.iso")
            return None
        except launch_service.LaunchRefused as e:
            return e
        finally:
            finish_prepare.set()
            await first

    refused = asyncio.run(scenario())
    assert refused is not None and refused.status == 409, "the second launch went ahead"
    assert calls["prepare"] == 1, "the refused launch prepared over the running game"
    assert calls["release"] == 0, "the refused launch reset the running game's saves"
    assert calls["spawn"] == 1


def test_the_claim_is_released_after_a_failed_launch(staged, monkeypatch):
    calls, _inside, finish_prepare = staged
    finish_prepare.set()

    async def broken_spawn(*_args):
        raise launch_service.LaunchRefused(503, "not installed")

    monkeypatch.setattr(launch_service, "_spawn", broken_spawn)

    async def scenario():
        statuses = []
        for _ in range(2):
            try:
                await launch_service.launch(SYSTEM, "dolphin", "", "a.iso")
            except launch_service.LaunchRefused as e:
                statuses.append(e.status)
        return statuses

    assert asyncio.run(scenario()) == [503, 503]
    assert calls["prepare"] == 2, "a failed launch must not block the next one"
