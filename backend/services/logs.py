"""The logs directory: one folder per section, the PC's only log (no journal).

    <DATA>/logs/backend/backend.log      every warning and error, whoever wrote it
    <DATA>/logs/<section>/<section>.log  one area's story, from INFO (SECTIONS)
    <DATA>/logs/launch/<system>/*.log    each launch's own output, newest kept
    <DATA>/logs/packs/<system>/          a pack script's own log (child_env)

Every file is capped, and every directory is created on the write that needs
it, so a purge (`purge()`) never needs a restart. Nothing here names an
emulator: the launch section is whatever `process_manager` starts.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import subprocess
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import logs_dir

SECTION_MAX_BYTES = 2 * 1024 * 1024
SECTION_BACKUPS = 2
LAUNCHES_KEPT = 10
# launch/ and packs/ are written by other processes, in append mode, so `run()`
# can empty a file that grows past the cap while its writer keeps going.
LAUNCH_MAX_BYTES = 4 * 1024 * 1024
CAP_EVERY = 30.0
WRITTEN_BY_CHILDREN = ("launch", "packs")
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# What the interface reports (`POST /api/logs/ui`) is logged under this name.
UI_LOGGER = "gamecore.ui"
UI_MESSAGE_MAX = 2000

# Section → the backend modules (logger names) that write it. GameCore's own
# modules only: what an emulator prints lands in launch/, whatever it is.
SECTIONS = {
    "controllers": ("backend.services.gamepad_monitor", "backend.services.gamepad_devices",
                    "backend.services.controller_autoconfig", "backend.services.controller_capture",
                    "backend.services.controller_profiles", "backend.services.controller_registry",
                    "backend.services.controller_roster", "backend.services.configgen",
                    "backend.services.usb_devices"),
    "media": ("backend.services.gamemedia", "backend.services.cover_pipeline",
              "backend.services.prefetch", "backend.services.metadata",
              "backend.services.scraper", "backend.services.local_media"),
    "session": ("backend.services.process_manager", "backend.services.launch",
                "backend.services.session", "backend.services.standby",
                "backend.services.desktop_power", "backend.services.fullscreen_enforcer",
                "backend.services.window_focus"),
    "network": ("backend.routers.settings.wifi", "backend.routers.settings.bluetooth",
                "backend.routers.settings.audio"),
    "ota": ("backend.routers.update",),
    "addons": ("backend.routers.addons",),
    "ui": (UI_LOGGER,),
}

log = logging.getLogger(__name__)


class SectionFile(RotatingFileHandler):
    """A capped log file that recreates its directory, e.g. after a purge."""

    def __init__(self, path: Path):
        super().__init__(path, maxBytes=SECTION_MAX_BYTES,
                         backupCount=SECTION_BACKUPS, delay=True, encoding="utf-8")
        self.setFormatter(logging.Formatter(FORMAT))

    def _open(self):
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()


def _handlers() -> list[SectionFile]:
    loggers = [logging.getLogger()] + [logging.getLogger(n) for ns in SECTIONS.values() for n in ns]
    return [h for lg in loggers for h in lg.handlers if isinstance(h, SectionFile)]


def install() -> None:
    """Attach the section files. Idempotent.

    A section's modules log from INFO: their INFO lines are the story a file is
    for, and they reach the journal too, as standby's already did.
    """
    if _handlers():
        return
    backend = SectionFile(logs_dir() / "backend" / "backend.log")
    backend.setLevel(logging.WARNING)
    logging.getLogger().addHandler(backend)
    for section, names in SECTIONS.items():
        handler = SectionFile(logs_dir() / section / f"{section}.log")
        for name in names:
            logger = logging.getLogger(name)
            if logger.getEffectiveLevel() > logging.INFO:
                logger.setLevel(logging.INFO)
            logger.addHandler(handler)


def child_env(system_id: str) -> dict:
    """What a launched process is told: where a pack's own log may go."""
    return {"GAMECORE_LOG_DIR": str(logs_dir() / "packs" / _safe(system_id or "unknown"))}


def mask_mac(mac: str) -> str:
    """`AA:BB:CC:xx:xx:FF`: enough to tell two devices apart, not to track one."""
    parts = mac.split(":")
    return ":".join(parts[:3] + ["xx", "xx"] + parts[5:]) if len(parts) == 6 else "xx"


def _safe(name: str) -> str:
    return _UNSAFE.sub("_", Path(name).name).strip("._")[:80] or "unknown"


def one_line(text: str) -> str:
    """A reported string on one log line: a newline in it cannot forge a record."""
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _tidy_launches(folder: Path) -> None:
    """Keep the newest LAUNCHES_KEPT - 1 logs; names start with the time."""
    for old in sorted(folder.glob("*.log"), reverse=True)[LAUNCHES_KEPT - 1:]:
        old.unlink(missing_ok=True)


@contextlib.contextmanager
def launch_output(system_id: str, game: str, cmd: list[str]):
    """The file a launch writes its stdout and stderr to; DEVNULL if it cannot
    be opened. Never fatal: a log must not cost the player the game."""
    out = None
    try:
        folder = logs_dir() / "launch" / _safe(system_id or "unknown")
        folder.mkdir(parents=True, exist_ok=True)
        _tidy_launches(folder)
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{_safe(game)}.log"
        out = path.open("ab")
        out.write(f"$ {' '.join(cmd)}\n".encode())
        out.flush()
    except OSError:
        if out is not None:
            out.close()
        log.warning("logs: no launch log for %s", system_id, exc_info=True)
        yield subprocess.DEVNULL
        return
    with out:
        yield out


def _child_files() -> list[Path]:
    root = logs_dir()
    return [p for d in WRITTEN_BY_CHILDREN if (root / d).is_dir()
            for p in (root / d).rglob("*") if p.is_file()]


def cap_child_logs() -> None:
    """Empty a launch or pack log past LAUNCH_MAX_BYTES; its writer appends on."""
    for path in _child_files():
        try:
            if path.stat().st_size > LAUNCH_MAX_BYTES:
                os.truncate(path, 0)
                with path.open("ab") as f:
                    f.write(b"[earlier output cut: over the size cap]\n")
        except OSError:
            continue


async def run() -> None:
    """Keep launch and pack logs under their cap while they are being written."""
    while True:
        await asyncio.sleep(CAP_EVERY)
        await asyncio.to_thread(cap_child_logs)


def usage() -> dict:
    """How much the logs directory holds; an emptied file does not count."""
    root = logs_dir()
    sizes = []
    for p in (root.rglob("*") if root.is_dir() else []):
        try:
            if p.is_file() and (size := p.stat().st_size):
                sizes.append(size)
        except OSError:
            continue        # rotated or tidied away while walking
    return {"files": len(sizes), "bytes": sum(sizes)}


def _remove_or_empty(path: Path) -> None:
    """Delete `path`; one another process holds open (a suspended game's log:
    Windows refuses the delete) is emptied instead."""
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        with contextlib.suppress(OSError):
            os.truncate(path, 0)


def _empty(root: Path) -> None:
    """Delete the logs. A file another process may still write (the running
    game's, a pack's) is emptied instead, so its space comes back now."""
    for child in (root.iterdir() if root.is_dir() else []):
        if child.name in WRITTEN_BY_CHILDREN and child.is_dir():
            for folder in (d for d in child.iterdir() if d.is_dir()):
                files = sorted(p for p in folder.iterdir() if p.is_file())
                still_open = files if child.name == "packs" else files[-1:]
                for p in files:
                    if p in still_open:
                        os.truncate(p, 0)
                    else:
                        _remove_or_empty(p)
        elif child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            _remove_or_empty(child)


def purge() -> dict:
    """Delete every log. Returns what was freed, as `usage()` measured it.

    Every section file stays locked until the files are gone: a record written
    in between would reopen a file the delete then removes, and that section
    would log into a deleted file. Each reopens on its next record.
    """
    freed = usage()
    handlers = _handlers()
    for h in handlers:
        h.acquire()
    try:
        for h in handlers:
            h.close()
        _empty(logs_dir())
    finally:
        for h in handlers:
            h.release()
    log.info("logs: purged %d files, %d bytes", freed["files"], freed["bytes"])
    return freed
