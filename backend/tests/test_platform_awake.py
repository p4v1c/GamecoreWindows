"""Standby's dark screen keeps the PC running, as DPMS did on the Linux box
(dev-log D56); Rest mode's sleep lets it go (D62).

Fakes only: the power request, the monitor message and the sleep model. The
real screen_off/screen_on/suspend are taken at import, before conftest replaces them.
"""
from __future__ import annotations

import pytest

from backend.services.platform import awake, power, sleep

REAL_SCREEN_OFF = power.screen_off
REAL_SCREEN_ON = power.screen_on
REAL_SUSPEND = sleep.suspend
REAL_SLEEP_DISPLAY = power.sleep_display


class FakeRequest:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def set(self, kind):
        self.calls.append(("set", kind))

    def clear(self, kind):
        self.calls.append(("clear", kind))


@pytest.fixture
def pc(monkeypatch):
    """A PC whose power request, monitor and sleep model are recorded, not real."""
    request = FakeRequest()
    monitor: list[int] = []
    model = {"value": sleep.MODERN_STANDBY}
    monkeypatch.setattr(awake, "_request", request)
    monkeypatch.setattr(awake, "_held", set())
    monkeypatch.setattr(power, "_monitor", monitor.append)
    monkeypatch.setattr(power, "signal_activity", lambda: None)
    monkeypatch.setattr(power, "_user32", lambda: type("U", (), {"SendInput": staticmethod(lambda *a: 1)})())
    monkeypatch.setattr(sleep, "power_model", lambda: model["value"])
    return request, monitor, model


def test_modern_standby_keeps_the_display_on_and_the_pc_running(pc):
    request, monitor, _ = pc
    REAL_SCREEN_OFF()
    assert awake.held() == {awake.DISPLAY_REQUIRED, awake.SYSTEM_REQUIRED}
    # SC_MONITORPOWER off is what took this PC into Modern Standby.
    assert power._MONITOR_OFF not in monitor


def test_s3_switches_the_screen_off_and_holds_the_pc_awake(pc):
    request, monitor, model = pc
    model["value"] = sleep.S3
    REAL_SCREEN_OFF()
    assert monitor == [power._MONITOR_OFF]
    assert awake.held() == {awake.SYSTEM_REQUIRED}


def test_the_screen_back_on_releases_every_hold(pc):
    request, monitor, _ = pc
    REAL_SCREEN_OFF()
    REAL_SCREEN_ON()
    assert awake.held() == set()
    assert ("clear", awake.DISPLAY_REQUIRED) in request.calls
    assert ("clear", awake.SYSTEM_REQUIRED) in request.calls
    assert monitor[-1] == power._MONITOR_ON


def test_a_second_screen_off_does_not_stack_requests(pc):
    request, _, _ = pc
    REAL_SCREEN_OFF()
    REAL_SCREEN_OFF()
    assert request.calls.count(("set", awake.DISPLAY_REQUIRED)) == 1
    assert request.calls.count(("set", awake.SYSTEM_REQUIRED)) == 1


def test_an_unknown_sleep_model_falls_back_to_switching_the_screen_off(pc, monkeypatch):
    _, monitor, _ = pc

    def unknown():
        raise OSError("GetPwrCapabilities failed")

    monkeypatch.setattr(sleep, "power_model", unknown)
    REAL_SCREEN_OFF()
    assert monitor == [power._MONITOR_OFF]
    assert awake.held() == {awake.SYSTEM_REQUIRED}


def test_release_before_any_hold_is_harmless(monkeypatch):
    monkeypatch.setattr(awake, "_request", None)
    monkeypatch.setattr(awake, "_held", set())
    awake.release()
    assert awake.held() == set()


def test_rest_on_modern_standby_lets_go_of_every_hold_and_switches_the_display_off(pc, monkeypatch):
    """The display off with nothing held is Windows' own way into Modern Standby."""
    request, monitor, _ = pc
    monkeypatch.setattr(sleep, "_powrprof", lambda: pytest.fail("SetSuspendState hibernates this PC"))
    monkeypatch.setattr(power, "sleep_display", REAL_SLEEP_DISPLAY)
    REAL_SCREEN_OFF()                     # standby had the screen dark and held
    assert REAL_SUSPEND() == (True, sleep.MODERN_STANDBY)
    assert awake.held() == set()
    assert monitor[-1] == power._MONITOR_OFF


def test_a_pc_with_no_sleep_state_refuses_rest_mode(pc):
    _, monitor, model = pc
    model["value"] = sleep.NO_SLEEP
    ok, _why = REAL_SUSPEND()
    assert not ok and monitor == []
