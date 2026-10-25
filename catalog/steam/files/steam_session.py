"""Run Steam in Big Picture for one GameCore session, and last as long as Steam does.

The session's Job Object holds this process and everything it starts. A Steam
already running (it autostarts on Windows) is taken into that job instead of
being closed and started again: its process tree joins the job, and the
running instance is asked to open Big Picture, which takes under a second
where a restart took about nine. Kill, suspend, Rest mode and the window
hand-off then reach it like a Steam started here. If it cannot be taken in,
it is asked to close and Steam starts fresh inside the job, the way the Linux
box starts it.

Stdlib only: it runs on the installation's base interpreter.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, NamedTuple

STEAM_KEY = r"Software\Valve\Steam"
STEAM_IMAGE = "steam.exe"
SHUTDOWN_TIMEOUT_SECS = 60
POLL_SECS = 2.0
# Steam's self-update exits steam.exe and starts a new one a few seconds later.
RESTART_GRACE_SECS = 20.0
#: The job the backend started this process in (backend/services/platform/jobs.py).
JOB_ENV = "GAMECORE_JOB"
#: What a running Steam answers with Big Picture; `-gamepadui` handed to it opens nothing.
BIG_PICTURE_URL = "steam://open/bigpicture"


class Proc(NamedTuple):
    pid: int
    parent: int
    image: str
    created: int


def default_exe() -> Path:
    base = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    return Path(base) / "Steam" / STEAM_IMAGE


def registry_exe() -> str:
    """HKCU SteamExe as Steam writes it ('c:/program files (x86)/steam/steam.exe'), or ''."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STEAM_KEY) as key:
            return str(winreg.QueryValueEx(key, "SteamExe")[0])
    except (ImportError, OSError):
        return ""


def find_steam(read_registry: Callable[[], str] = registry_exe,
               fallback: Callable[[], Path] = default_exe) -> Path | None:
    """steam.exe from the registry, then the default folder; None when neither exists."""
    for candidate in (read_registry(), str(fallback())):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def parse_tasklist(text: str, image: str = STEAM_IMAGE) -> set[int]:
    """PIDs of `image` in `tasklist /FO CSV /NH` output; the 'no tasks' line is localised, so rows are parsed."""
    pids = set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].lower() == image and row[1].isdigit():
            pids.add(int(row[1]))
    return pids


def steam_pids() -> set[int]:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {STEAM_IMAGE}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return parse_tasklist(out or "")


def wait_until(condition: Callable[[], bool], timeout: float,
               sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic) -> bool:
    deadline = clock() + timeout
    while not condition():
        if clock() >= deadline:
            return False
        sleep(POLL_SECS)
    return True


def close_running_steam(exe: Path, running: Callable[[], set[int]] = steam_pids,
                        run: Callable[..., object] = subprocess.run,
                        sleep: Callable[[float], None] = time.sleep,
                        clock: Callable[[], float] = time.monotonic) -> bool:
    """Ask a Steam started outside this session to quit. False if it is still up after the timeout."""
    if not running():
        return True
    print("steam: could not take the running Steam into the session, asking it to close", flush=True)
    try:
        run([str(exe), "-shutdown"], timeout=SHUTDOWN_TIMEOUT_SECS)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"steam: -shutdown failed: {e}", flush=True)
    return wait_until(lambda: not running(), SHUTDOWN_TIMEOUT_SECS, sleep, clock)


def steam_tree(procs: list[Proc], image: str = STEAM_IMAGE) -> list[int]:
    """Every `image` process and all its descendants, parents first. Pure.

    A child counts only if it was created after its parent: Windows reuses
    process ids, and a dead parent's id may belong to a stranger now.
    """
    by_pid = {p.pid: p for p in procs}
    tree = [p.pid for p in procs if p.image.lower() == image]
    seen = set(tree)
    i = 0
    while i < len(tree):
        parent = by_pid[tree[i]]
        for p in procs:
            if p.parent == parent.pid and p.pid not in seen and p.created >= parent.created:
                tree.append(p.pid)
                seen.add(p.pid)
        i += 1
    return tree


def _processes() -> list[Proc]:
    """Every process: id, parent, image, creation time (Toolhelp32 snapshot)."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    procs = []
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    try:
        ok = kernel32.Process32FirstW(wintypes.HANDLE(snapshot), ctypes.byref(entry))
        while ok:
            procs.append(Proc(entry.th32ProcessID, entry.th32ParentProcessID,
                              entry.szExeFile, _created(kernel32, entry.th32ProcessID)))
            ok = kernel32.Process32NextW(wintypes.HANDLE(snapshot), ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(snapshot))
    return procs


def _created(kernel32, pid: int) -> int:
    import ctypes
    from ctypes import wintypes
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0
    times = [wintypes.FILETIME() for _ in range(4)]
    try:
        if not kernel32.GetProcessTimes(wintypes.HANDLE(handle), *map(ctypes.byref, times)):
            return 0
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def adopt(pids: list[int], job_name: str) -> list[int]:
    """Put `pids` into the session's job. Returns the ones that joined.

    A process that refuses (gone, protected, or in a job that cannot nest) is
    skipped; its children started from now on are born in the job anyway.
    """
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    job = kernel32.OpenJobObjectW(0x0001, False, job_name)   # JOB_OBJECT_ASSIGN_PROCESS
    if not job:
        return []
    joined = []
    try:
        for pid in pids:
            process = kernel32.OpenProcess(0x0101, False, pid)   # SET_QUOTA | TERMINATE
            if not process:
                continue
            try:
                if kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(process)):
                    joined.append(pid)
            finally:
                kernel32.CloseHandle(wintypes.HANDLE(process))
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(job))
    return joined


def take_running_steam(job_name: str, processes: Callable[[], list[Proc]] = _processes,
                       join: Callable[[list[int], str], list[int]] = adopt) -> bool:
    """Bring a running Steam into the session. True when every steam.exe joined."""
    procs = processes()
    tree = steam_tree(procs)
    roots = {p.pid for p in procs if p.image.lower() == STEAM_IMAGE}
    if not job_name or not roots:
        return False
    joined = set(join(tree, job_name))
    print(f"steam: {len(joined)} of {len(tree)} Steam processes joined the session", flush=True)
    return roots <= joined


def wait_for_steam_to_end(running: Callable[[], set[int]] = steam_pids,
                          sleep: Callable[[float], None] = time.sleep,
                          clock: Callable[[], float] = time.monotonic) -> None:
    """Return once no steam.exe has been seen for RESTART_GRACE_SECS."""
    while True:
        while running():
            sleep(POLL_SECS)
        if not wait_until(running, RESTART_GRACE_SECS, sleep, clock):
            return


def start_steam(exe: Path, steam_args: list[str]) -> int:
    """Big Picture inside the session: the running Steam taken in, else a fresh one. 0, or 3."""
    if steam_pids() and take_running_steam(os.environ.get(JOB_ENV, "")):
        print(f"steam: running Steam taken into the session, opening {BIG_PICTURE_URL}", flush=True)
        subprocess.Popen([str(exe), BIG_PICTURE_URL]).wait()
        return 0
    if not close_running_steam(exe):
        print("steam: the running Steam did not close; quit it, then launch again.", file=sys.stderr)
        return 3
    print(f"steam: starting {exe} {' '.join(steam_args)}", flush=True)
    subprocess.Popen([str(exe), *steam_args]).wait()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     epilog="Other arguments go to steam.exe (the pack sends -gamepadui).")
    _, steam_args = parser.parse_known_args(argv)
    exe = find_steam()
    if exe is None:
        print(f"steam: steam.exe not found (registry HKCU\\{STEAM_KEY}\\SteamExe, "
              f"{default_exe()}). Install Steam, then launch again.", file=sys.stderr)
        return 2
    code = start_steam(exe, steam_args)
    if code:
        return code
    wait_for_steam_to_end()
    print("steam: closed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
