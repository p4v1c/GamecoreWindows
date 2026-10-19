"""Tell the process group we started from one that merely reuses its number.

A pgid alone survives a power cut in session.json but not in the kernel: after
a reboot it can name any process. Boot id + the leader's start time cannot.
Both come from the OS through `services/platform/processes.py`.
"""
from .platform import processes


def boot_id() -> str:
    """This boot's id, or "" when the OS cannot say."""
    return processes.boot_id()


def start_time(pid: int) -> str:
    """The start time of `pid`, or "" when it does not exist or is unreadable."""
    return processes.start_time(pid)


def matches(entry: dict, pgid: int) -> bool:
    """Whether a saved session still names the group it was saved for.

    An entry without both fields (an older build) never matches: adopting a
    stranger's group is worse than losing track of our own.
    """
    saved_boot = str(entry.get("boot_id") or "")
    saved_start = str(entry.get("leader_start") or "")
    if not saved_boot or not saved_start:
        return False
    return saved_boot == boot_id() and saved_start == start_time(pgid)
