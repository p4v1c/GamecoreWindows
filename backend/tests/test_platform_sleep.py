"""`platform/sleep.py` and `platform/device_wake.py`: the pure parts, and the real
reads (sleep model, wake log, wake settings, a power-event registration).

The suite replaces the calls that sleep or change a device (conftest); the
real ones are taken at import, before that guard is in place. Nothing here
puts the PC to sleep or changes a wake setting.
"""
from __future__ import annotations

import ctypes

from backend.services.platform import device_wake, sleep

REAL_WATCH = sleep.watch
REAL_LAST_WAKE = sleep.last_wake
REAL_WAKE_DEVICES = device_wake.wake_devices

# A real event 1 of Microsoft-Windows-Power-Troubleshooter, from this PC (24/10/2026).
WAKE_XML = (
    "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>"
    "<Provider Name='Microsoft-Windows-Power-Troubleshooter'/><EventID>1</EventID>"
    "<EventRecordID>230365</EventRecordID></System><EventData>"
    "<Data Name='SleepTime'>2026-10-20T18:10:15.8869807Z</Data>"
    "<Data Name='TargetState'>6</Data><Data Name='EffectiveState'>5</Data>"
    "<Data Name='WakeSourceType'>0</Data><Data Name='WakeSourceTextLength'>0</Data>"
    "<Data Name='WakeSourceText'></Data></EventData></Event>")


def test_the_sleep_model_comes_from_the_two_capability_flags():
    assert sleep.model_of(s3=True, aoac=False) == sleep.S3
    assert sleep.model_of(s3=False, aoac=True) == sleep.MODERN_STANDBY
    assert sleep.model_of(s3=True, aoac=True) == sleep.MODERN_STANDBY
    assert sleep.model_of(s3=False, aoac=False) == sleep.NO_SLEEP


def test_the_capabilities_buffer_holds_everything_windows_writes():
    # SYSTEM_POWER_CAPABILITIES is 76 bytes; a shorter buffer is overwritten past its end.
    assert ctypes.sizeof(sleep._Capabilities) >= 76
    assert sleep._Capabilities.AoAc.offset == 20


def test_the_display_state_maps_off_and_on_and_ignores_dimmed():
    assert sleep.display_event(0) == sleep.DISPLAY_OFF
    assert sleep.display_event(1) == sleep.DISPLAY_ON
    assert sleep.display_event(2) is None


def test_a_wake_event_is_read_from_its_untranslated_field_names():
    assert sleep.parse_wake_event(WAKE_XML) == {"record": 230365, "source": "", "type": 0}
    named = WAKE_XML.replace("<Data Name='WakeSourceText'></Data>",
                             "<Data Name='WakeSourceText'>Power Button</Data>")
    assert sleep.parse_wake_event(named)["source"] == "Power Button"


def test_an_unreadable_wake_event_is_none():
    assert sleep.parse_wake_event("not xml") is None
    assert sleep.parse_wake_event("<Event/>") is None


def test_a_wmi_instance_name_gives_back_the_pnp_id():
    assert device_wake.instance_id("USB\\VID_8087&PID_0029\\5&2B4F1E1&0&10_0") == \
        "USB\\VID_8087&PID_0029\\5&2B4F1E1&0&10"
    assert device_wake.instance_id("ACPI\\PNP0C0C\\2&DABA3FF&0") == "ACPI\\PNP0C0C\\2&DABA3FF&0"


def test_the_real_sleep_model_reads():
    assert sleep.power_model() in {sleep.S3, sleep.MODERN_STANDBY, sleep.NO_SLEEP}


def test_the_real_wake_log_reads_without_privilege():
    event = REAL_LAST_WAKE()
    assert event is None or (isinstance(event["record"], int) and isinstance(event["source"], str))


def test_the_real_wake_settings_read_or_list_nothing_unelevated():
    devices = REAL_WAKE_DEVICES()
    assert isinstance(devices, list)
    assert all({"instance", "wmi_name", "enabled", "pnp_class"} <= set(d) for d in devices)


def test_a_real_power_event_registration_starts_and_stops():
    """Windows reports the console display state once at registration."""
    import time
    seen: list[str] = []
    stop = REAL_WATCH(seen.append)
    try:
        for _ in range(50):
            if seen:
                break
            time.sleep(0.02)
    finally:
        stop()
    assert set(seen) <= {sleep.DISPLAY_ON, sleep.DISPLAY_OFF}
