"""Which boot this is, and when a process started.

Together they tell a game we launched from a process that merely reuses its
PID after a reboot (`services/process_identity.py`). Also: killing a tree
that has no job, and how much memory the PC has.
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


def kill_tree(pid: int) -> None:
    """Kill `pid` and every process it started. Nothing happens for a pid
    that is gone. For a child started without a job (the update script);
    games live in a job (`jobs.py`)."""
    try:
        leader = psutil.Process(pid)
        tree = [*leader.children(recursive=True), leader]
    except (psutil.Error, ValueError):
        return
    for proc in tree:
        try:
            proc.kill()
        except psutil.Error:
            pass


def physical_memory() -> int:
    """Installed RAM in bytes, 0 when unknown."""
    try:
        return int(psutil.virtual_memory().total)
    except (psutil.Error, OSError):
        return 0
