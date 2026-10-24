"""After a wake: wait for an active monitor, bounded, before the UI is told.

The probe is a fake in every timing test; one test reads the real outputs.
"""
from __future__ import annotations

import asyncio
import time

from backend.services import display_ready, standby
from backend.services.platform import display as platform_display


def test_it_waits_for_the_monitor_to_come_back(monkeypatch):
    monkeypatch.setattr(display_ready, "POLL_SECS", 0.01)
    back_at = time.monotonic() + 0.1

    start = time.monotonic()
    ok = asyncio.run(display_ready.wait_for_display(
        0, probe=lambda: time.monotonic() >= back_at, max_wait=2))
    assert ok and 0.09 <= time.monotonic() - start < 1.0


def test_no_screen_gives_up_after_the_bound_and_wakes_anyway(monkeypatch):
    monkeypatch.setattr(display_ready, "POLL_SECS", 0.01)
    start = time.monotonic()
    assert asyncio.run(display_ready.wait_for_display(0, probe=lambda: False,
                                                      max_wait=0.1)) is False
    assert time.monotonic() - start < 0.5


def test_a_screen_already_lit_is_not_waited_for():
    start = time.monotonic()
    assert asyncio.run(display_ready.wait_for_display(0, probe=lambda: True, max_wait=5))
    assert time.monotonic() - start < 0.5


def test_a_probe_windows_refuses_counts_as_not_ready(monkeypatch):
    def refuse():
        raise OSError("EnumDisplayDevices failed")
    monkeypatch.setattr(platform_display, "display_ready", refuse)
    assert display_ready._probe() is False


def test_the_settle_delay_is_a_setting_clamped_to_0_8(monkeypatch, tmp_path):
    monkeypatch.setattr(standby, "CONFIG_FILE", tmp_path / "standby.json")
    assert standby.load_config()["wake_settle_secs"] == 2.5
    assert standby.save_config({"wake_settle_secs": 4})["wake_settle_secs"] == 4.0
    assert standby.save_config({"wake_settle_secs": 30})["wake_settle_secs"] == 8.0
    assert standby.save_config({"wake_settle_secs": -1})["wake_settle_secs"] == 0.0
    assert standby.load_config()["wake_settle_secs"] == 0.0


def test_the_real_outputs_answer_without_changing_anything():
    assert platform_display.display_ready() in (True, False)
