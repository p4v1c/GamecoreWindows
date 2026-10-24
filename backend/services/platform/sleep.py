"""Sleep: which kind this PC has, entering it, hearing it come and go, and why it woke.

Replaces `systemctl suspend`, logind's `PrepareForSleep` (read through
`gdbus monitor`) and `/sys/class/wakeup`:

| Here | Win32 |
|---|---|
| `power_model()` | `GetPwrCapabilities`: S3 sleep, Modern Standby (S0 low-power idle, `AoAc`), or neither |
| `suspend()` | S3: `SetSuspendState` with the shutdown privilege enabled. Modern Standby has no call to enter it: turning the screen off is what starts it (its "Screen off" phase), so that is what this does |
| `watch()` | `PowerRegisterSuspendResumeNotification` (suspend, automatic resume) and `PowerSettingRegisterNotification` on the console display state, both as callbacks: no window needed |
| `last_wake()` | the newest `Power-Troubleshooter` event 1 of the System log, read as XML (its field names are never translated) |

A Modern Standby PC is not promised the suspend and resume callbacks (Desktop
Activity Moderator freezes desktop apps instead), so `watch()` also reports
the console display going off and on; the service decides what that means.
"""
from __future__ import annotations

import ctypes
import logging
import subprocess
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from ctypes import wintypes

from . import power

log = logging.getLogger(__name__)

S3, MODERN_STANDBY, NO_SLEEP = "s3", "modern_standby", "none"
SUSPEND, RESUME, DISPLAY_OFF, DISPLAY_ON = "suspend", "resume", "display_off", "display_on"

_PBT_APMSUSPEND = 0x0004
_PBT_APMRESUMEAUTOMATIC = 0x0012
_PBT_POWERSETTINGCHANGE = 0x8013
_DEVICE_NOTIFY_CALLBACK = 2
_CONSOLE_DISPLAY_STATE = "6fe69556-704a-47a0-8f24-c28d936fda47"
_DISPLAY_EVENTS = {0: DISPLAY_OFF, 1: DISPLAY_ON}   # 2 = dimmed: still lit

_WAKE_QUERY = ("*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter']"
               " and (EventID=1)]]")
_WEVTUTIL_SECS = 10.0
_EVENT_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


class _Capabilities(ctypes.Structure):
    """SYSTEM_POWER_CAPABILITIES up to `AoAc`; the tail is padded past its 76 bytes."""
    _fields_ = [("PowerButtonPresent", ctypes.c_ubyte), ("SleepButtonPresent", ctypes.c_ubyte),
                ("LidPresent", ctypes.c_ubyte), ("SystemS1", ctypes.c_ubyte),
                ("SystemS2", ctypes.c_ubyte), ("SystemS3", ctypes.c_ubyte),
                ("SystemS4", ctypes.c_ubyte), ("SystemS5", ctypes.c_ubyte),
                ("HiberFilePresent", ctypes.c_ubyte), ("FullWake", ctypes.c_ubyte),
                ("VideoDimPresent", ctypes.c_ubyte), ("ApmPresent", ctypes.c_ubyte),
                ("UpsPresent", ctypes.c_ubyte), ("ThermalControl", ctypes.c_ubyte),
                ("ProcessorThrottle", ctypes.c_ubyte), ("ProcessorMinThrottle", ctypes.c_ubyte),
                ("ProcessorMaxThrottle", ctypes.c_ubyte), ("FastSystemS4", ctypes.c_ubyte),
                ("Hiberboot", ctypes.c_ubyte), ("WakeAlarmPresent", ctypes.c_ubyte),
                ("AoAc", ctypes.c_ubyte), ("rest", ctypes.c_ubyte * 107)]


class _GUID(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_byte * 16)]


class _Subscribe(ctypes.Structure):
    """DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS."""
    _fields_ = [("Callback", ctypes.c_void_p), ("Context", ctypes.c_void_p)]


class _PowerSetting(ctypes.Structure):
    """POWERBROADCAST_SETTING, with room for the DWORD the display state carries."""
    _fields_ = [("PowerSetting", _GUID), ("DataLength", wintypes.DWORD),
                ("Data", wintypes.DWORD)]


_CALLBACK = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p)


def _powrprof():
    return ctypes.WinDLL("powrprof", use_last_error=True)


def model_of(s3: bool, aoac: bool) -> str:
    """The sleep a PC offers, from its two capability flags. Pure."""
    if aoac:
        return MODERN_STANDBY
    return S3 if s3 else NO_SLEEP


def power_model() -> str:
    """`S3`, `MODERN_STANDBY` or `NO_SLEEP`. Raises OSError when Windows will not say."""
    caps = _Capabilities()
    if not _powrprof().GetPwrCapabilities(ctypes.byref(caps)):
        raise OSError(ctypes.get_last_error(), "GetPwrCapabilities failed")
    return model_of(bool(caps.SystemS3), bool(caps.AoAc))


def _enable_shutdown_privilege() -> None:
    # Held by every interactive user but disabled: SetSuspendState refuses without it.
    import win32api
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(),
        win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY)
    luid = win32security.LookupPrivilegeValue(None, win32security.SE_SHUTDOWN_NAME)
    win32security.AdjustTokenPrivileges(token, False,
                                        [(luid, win32security.SE_PRIVILEGE_ENABLED)])


def suspend() -> tuple[bool, str]:
    """Put the PC to sleep. (True, how) when asked, (False, why) when refused.

    S3 blocks until the wake. Modern Standby returns at once: the screen goes
    off and Windows takes the PC down from there.
    """
    try:
        model = power_model()
    except OSError as e:
        return False, str(e)
    if model == MODERN_STANDBY:
        power.screen_off()
        return True, "modern standby: screen off"
    if model == NO_SLEEP:
        return False, "this PC offers no sleep state"
    try:
        _enable_shutdown_privilege()
    except Exception as e:  # pywintypes.error is not an OSError
        return False, f"no shutdown privilege: {e}"
    if not _powrprof().SetSuspendState(False, False, False):
        return False, f"SetSuspendState refused (error {ctypes.get_last_error()})"
    return True, "s3"


def display_event(state: int) -> str | None:
    """`DISPLAY_OFF`, `DISPLAY_ON`, or None for dimmed / unknown. Pure."""
    return _DISPLAY_EVENTS.get(state)


def watch(handler: Callable[[str], None]) -> Callable[[], None]:
    """Call `handler(event)` on Windows' power thread for every sleep and wake.

    Events: `SUSPEND` (Windows waits about 2 s for the handler to return before
    it sleeps anyway), `RESUME`, `DISPLAY_OFF`, `DISPLAY_ON` (also sent once at
    registration with the current state). Returns the function that stops it.
    Raises OSError when Windows refuses the registration.
    """
    def on_power(_context, kind, setting):
        try:
            if kind == _PBT_APMSUSPEND:
                handler(SUSPEND)
            elif kind == _PBT_APMRESUMEAUTOMATIC:
                handler(RESUME)
            elif kind == _PBT_POWERSETTINGCHANGE and setting:
                event = display_event(_PowerSetting.from_address(setting).Data)
                if event:
                    handler(event)
        except Exception:
            log.exception("sleep: a power event handler failed")
        return 0

    callback = _CALLBACK(on_power)
    params = _Subscribe(ctypes.cast(callback, ctypes.c_void_p), None)
    prof = _powrprof()
    suspend_handle, display_handle = ctypes.c_void_p(), ctypes.c_void_p()
    code = prof.PowerRegisterSuspendResumeNotification(
        _DEVICE_NOTIFY_CALLBACK, ctypes.byref(params), ctypes.byref(suspend_handle))
    if code:
        raise OSError(code, "PowerRegisterSuspendResumeNotification refused")
    guid = _GUID((ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(_CONSOLE_DISPLAY_STATE).bytes_le))
    code = prof.PowerSettingRegisterNotification(
        ctypes.byref(guid), _DEVICE_NOTIFY_CALLBACK, ctypes.byref(params),
        ctypes.byref(display_handle))
    if code:
        prof.PowerUnregisterSuspendResumeNotification(suspend_handle)
        raise OSError(code, "PowerSettingRegisterNotification refused")

    def stop() -> None:
        prof.PowerUnregisterSuspendResumeNotification(suspend_handle)
        prof.PowerSettingUnregisterNotification(display_handle)
        # Windows calls `callback` until the lines above return: keep it alive till then.
        stop.keep = None

    stop.keep = (callback, params)
    return stop


def parse_wake_event(xml_text: str) -> dict | None:
    """{record, source, type} of a Power-Troubleshooter event, or None. Pure.

    `source` is Windows' own name for what woke the PC (a device, "Power
    Button"), empty when it did not keep one.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    record = root.findtext(f"{_EVENT_NS}System/{_EVENT_NS}EventRecordID")
    if not (record or "").isdigit():
        return None
    data = {d.get("Name"): (d.text or "") for d in root.iter(f"{_EVENT_NS}Data")}
    kind = data.get("WakeSourceType", "")
    return {"record": int(record), "source": data.get("WakeSourceText", "").strip(),
            "type": int(kind) if kind.isdigit() else None}


def last_wake() -> dict | None:
    """The newest wake Windows logged (`parse_wake_event`), or None."""
    try:
        done = subprocess.run(
            ["wevtutil", "qe", "System", f"/q:{_WAKE_QUERY}", "/c:1", "/rd:true", "/f:xml"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_WEVTUTIL_SECS, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.debug("sleep: could not read the wake log: %s", e)
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    return parse_wake_event(done.stdout.strip())
