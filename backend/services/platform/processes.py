"""Which boot this is, and when a process started.

Together they tell a game we launched from a process that merely reuses its
PID after a reboot (`services/process_identity.py`).
"""
from __future__ import annotations

import winreg

import psutil

# Incremented by Windows on every boot. Unlike `psutil.boot_time()`, which is
# derived from the uptime and drifts by a second between calls, it is exact.
BOOT_ID_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"
BOOT_ID_VALUE = "BootId"


def boot_id() -> str:
    """This boot's id, or "" when Windows cannot say."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BOOT_ID_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, BOOT_ID_VALUE)
    except OSError:
        return ""
    return str(value)


def start_time(pid: int) -> str:
    """When `pid` started (epoch seconds, ms precision), or "" when it does
    not exist or cannot be read."""
    try:
        created = psutil.Process(pid).create_time()
    except (psutil.Error, ValueError):
        return ""
    return f"{created:.3f}"
