"""`platform/power.py`: the structures Windows reads, and the real plan, read only.

The suite replaces every call that changes the machine (conftest); the real
reads are taken at import, before that guard is in place.
"""
from __future__ import annotations

import ctypes

from backend.services.platform import power

REAL_ACTIVE_SCHEME = power.active_scheme
REAL_IDLE_TIMEOUTS = power.idle_timeouts


def test_a_guid_survives_the_trip_through_windows_byte_order():
    assert power._GUID.of(power.POWER_SAVER).text() == power.POWER_SAVER


def test_send_input_gets_the_structure_size_it_insists_on():
    # SendInput refuses any cbSize but sizeof(INPUT): 40 bytes on x64.
    assert ctypes.sizeof(power._INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_every_timer_names_a_subgroup_and_a_setting():
    assert set(power.TIMERS) == {"display_ac", "display_dc", "sleep_ac", "sleep_dc"}
    assert all(len(sub) == 36 and len(setting) == 36 for sub, setting, _ in power.TIMERS.values())


def test_the_real_plan_and_its_timers_read_without_changing_anything():
    scheme = REAL_ACTIVE_SCHEME()
    assert len(scheme) == 36
    timers = REAL_IDLE_TIMEOUTS()
    assert set(timers) == set(power.TIMERS)
    assert all(isinstance(v, int) and v >= 0 for v in timers.values())
