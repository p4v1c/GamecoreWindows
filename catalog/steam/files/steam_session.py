"""Run Steam in Big Picture for one GameCore session, and last as long as Steam does.

The session's Job Object holds this process and everything it starts, so Steam
is started from here. A Steam already running outside the job would take the
launch over and exit at once (Steam is single-instance), ending the session in
a second: it is asked to close first, the way the Linux box starts it fresh.

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
from typing import Callable

STEAM_KEY = r"Software\Valve\Steam"
STEAM_IMAGE = "steam.exe"
SHUTDOWN_TIMEOUT_SECS = 60
POLL_SECS = 2.0
# Steam's self-update exits steam.exe and starts a new one a few seconds later.
RESTART_GRACE_SECS = 20.0


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
    print("steam: already running outside GameCore, asking it to close", flush=True)
    try:
        run([str(exe), "-shutdown"], timeout=SHUTDOWN_TIMEOUT_SECS)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"steam: -shutdown failed: {e}", flush=True)
    return wait_until(lambda: not running(), SHUTDOWN_TIMEOUT_SECS, sleep, clock)


def wait_for_steam_to_end(running: Callable[[], set[int]] = steam_pids,
                          sleep: Callable[[float], None] = time.sleep,
                          clock: Callable[[], float] = time.monotonic) -> None:
    """Return once no steam.exe has been seen for RESTART_GRACE_SECS."""
    while True:
        while running():
            sleep(POLL_SECS)
        if not wait_until(running, RESTART_GRACE_SECS, sleep, clock):
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     epilog="Other arguments go to steam.exe (the pack sends -gamepadui).")
    _, steam_args = parser.parse_known_args(argv)
    exe = find_steam()
    if exe is None:
        print(f"steam: steam.exe not found (registry HKCU\\{STEAM_KEY}\\SteamExe, "
              f"{default_exe()}). Install Steam, then launch again.", file=sys.stderr)
        return 2
    if not close_running_steam(exe):
        print("steam: the running Steam did not close; quit it, then launch again.", file=sys.stderr)
        return 3
    print(f"steam: starting {exe} {' '.join(steam_args)}", flush=True)
    subprocess.Popen([str(exe), *steam_args]).wait()
    wait_for_steam_to_end()
    print("steam: closed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
