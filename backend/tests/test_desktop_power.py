"""Who owns the screen timeout: GameCore's standby, or Windows' power plan.

Windows' "turn off the display" and "sleep after" timers cannot see a pad, so
they capped GameCore's own timings. `desktop_power` takes them while standby
is on and must hand them back. The plan is faked in every test (`plan`);
nothing here writes this PC's power settings.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from backend.services import desktop_power as dp
from backend.services.platform import power as platform_power

WINDOWS = {"display_ac": 900, "display_dc": 180, "sleep_ac": 1800, "sleep_dc": 600}
NEVER = {k: 0 for k in WINDOWS}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def plan(monkeypatch, tmp_path):
    """A fake active plan: `timers` is what Windows holds, `writes` what was set."""
    state = {"timers": dict(WINDOWS), "writes": [], "refuse": False, "unreadable": False}

    def idle_timeouts():
        if state["unreadable"]:
            raise OSError("PowerReadACValueIndex failed")
        return dict(state["timers"])

    def set_idle_timeouts(values):
        state["writes"].append(dict(values))
        if state["refuse"]:
            raise OSError("PowerWriteACValueIndex failed")
        state["timers"].update(values)

    monkeypatch.setattr(platform_power, "idle_timeouts", idle_timeouts)
    monkeypatch.setattr(platform_power, "set_idle_timeouts", set_idle_timeouts)
    monkeypatch.setattr(dp, "_HANDOFF", tmp_path / "handoff.json")
    return state


def test_claiming_sets_both_windows_timers_to_never(plan):
    assert run(dp.claim()) is True
    assert plan["timers"] == NEVER


def test_the_previous_values_are_written_down_before_they_are_lost(plan):
    run(dp.claim())
    assert dp._note()[dp._KEY] == WINDOWS


def test_claiming_twice_does_not_forget_the_real_setting(plan):
    run(dp.claim())
    run(dp.claim())
    assert dp._note()[dp._KEY] == WINDOWS
    assert len(plan["writes"]) == 1, "an already-held claim was written again"


def test_releasing_gives_windows_its_timers_back(plan):
    run(dp.claim())
    assert run(dp.release()) is True
    assert plan["timers"] == WINDOWS


def test_a_release_leaves_nothing_behind_to_replay(plan):
    run(dp.claim())
    run(dp.release())
    assert not dp._HANDOFF.exists()
    assert run(dp.release()) is False


def test_releasing_without_a_claim_changes_nothing(plan):
    assert run(dp.release()) is False
    assert plan["writes"] == []


def test_claim_release_claim_still_knows_the_original(plan):
    run(dp.claim())
    run(dp.release())
    run(dp.claim())
    assert dp._note()[dp._KEY] == WINDOWS


def test_timers_already_at_never_are_ours_and_noted_nowhere(plan):
    plan["timers"] = dict(NEVER)
    assert run(dp.claim()) is True
    assert plan["writes"] == [] and not dp._HANDOFF.exists()


def test_a_plan_that_cannot_be_read_is_not_claimed(plan):
    plan["unreadable"] = True
    assert run(dp.claim()) is False
    assert not dp._HANDOFF.exists()


def test_a_claim_that_could_not_write_leaves_no_note_behind(plan):
    plan["refuse"] = True
    assert run(dp.claim()) is False
    assert not dp._HANDOFF.exists(), "a note for a claim that never happened"


def test_a_release_that_could_not_write_keeps_the_note(plan):
    run(dp.claim())
    plan["refuse"] = True
    assert run(dp.release()) is False
    assert dp._note()[dp._KEY] == WINDOWS


def test_a_note_from_the_linux_box_is_dropped_without_writing(plan):
    dp._HANDOFF.write_text('{"previous": "900", "x_previous": {"s": "600 600"}}')
    assert run(dp.release()) is False
    assert plan["writes"] == [] and not dp._HANDOFF.exists()


# ── the switch, end to end ───────────────────────────────────────────────────

def test_turning_standby_off_hands_the_screen_back(plan, monkeypatch, tmp_path):
    """The failure this whole module has to avoid: both disarmed at once."""
    from fastapi.testclient import TestClient

    from backend import main
    from backend.services import standby

    monkeypatch.setattr(standby, "CONFIG_FILE", tmp_path / "standby.json")
    run(dp.claim())
    with TestClient(main.app) as c:
        r = c.post("/api/standby/config", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert plan["timers"] == WINDOWS, "the television would never go dark again"


# ── the claim must be RE-MADE, not made once ────────────────────────────────

@pytest.fixture
def watcher(monkeypatch):
    """The standby watcher, with the claim replaced by a counter."""
    from backend.services import process_manager as pm
    from backend.services import standby

    attempts = []

    async def fake_claim():
        attempts.append("claim")
        return True

    async def quiet(*argv, **kw):
        return True

    monkeypatch.setattr(dp, "claim", fake_claim)
    monkeypatch.setattr(standby, "_power", quiet)
    monkeypatch.setattr(type(pm.process_manager), "is_foreground", property(lambda self: True))
    standby._last_claim_attempt = 0.0
    standby._state = "active"
    standby._last_input = time.monotonic()
    yield attempts
    standby._last_claim_attempt = 0.0
    standby._state = "active"


CFG_ON = {"enabled": True, "screensaver_mins": 10, "sleep_mins": 20}
CFG_OFF = {**CFG_ON, "enabled": False}


def test_the_watcher_claims_the_timers_before_the_foreground_return(watcher):
    from backend.services import standby
    asyncio.run(standby._tick(CFG_ON))
    assert watcher == ["claim"], "the foreground early return skipped the claim"


def test_it_is_not_re_attempted_every_fifteen_seconds(watcher):
    from backend.services import standby
    for _ in range(4):
        asyncio.run(standby._tick(CFG_ON))
    assert len(watcher) == 1


def test_a_minute_later_it_is_attempted_again(watcher):
    """The owner may change the plan's timeouts from Windows' own settings."""
    from backend.services import standby
    asyncio.run(standby._tick(CFG_ON))
    standby._last_claim_attempt -= standby._CLAIM_RETRY_SECS + 1
    asyncio.run(standby._tick(CFG_ON))
    assert len(watcher) == 2


def test_standby_switched_off_takes_nothing_back(watcher):
    """Off means Windows' own timers decide: the watcher must not re-take them."""
    from backend.services import standby
    asyncio.run(standby._tick(CFG_OFF))
    assert watcher == []
