"""The logs directory: launch output kept, files capped, purge never breaks logging.

Emulator output used to go to /dev/null, so a game that crashed left nothing
to read. Every path is under a throwaway data root (`paths.use_roots`).
"""
import asyncio
import logging
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import logs as logs_router
from backend.services import logs, paths
from backend.services import process_manager as pm


@pytest.fixture
def data(tmp_path, monkeypatch):
    before = (paths.GAMECORE_ROOT, paths.GAMECORE_DATA)
    paths.use_roots(tmp_path / "code", tmp_path / "data")
    yield tmp_path / "data" / "logs"
    paths.use_roots(*before)


def test_a_launch_keeps_what_the_emulator_printed(data, monkeypatch):
    """A real game started in a real job: its stdout and stderr land in the log."""
    manager = pm.ProcessManager()
    monkeypatch.setattr(pm.session, "launch_blocker", lambda: "")
    monkeypatch.setattr(pm.ws, "broadcast", AsyncMock())
    monkeypatch.setattr(pm.ws, "set_current_game", lambda data: None)
    monkeypatch.setattr(manager, "_save_state", lambda: None)
    monkeypatch.setattr(manager, "_clear_session", lambda: None)
    monkeypatch.setattr(manager, "_watch", AsyncMock())
    script = ("import os, sys; print('out'); print('err', file=sys.stderr); "
              "print('dir=' + os.environ['GAMECORE_LOG_DIR'])")

    async def scenario():
        await manager.launch(sys.executable, f'-c "{script}"',
                             game_key="Some Game (Europe).nds", system_id="melonds")
        job = manager._fg.job
        while job.alive():
            await asyncio.sleep(0.05)
        job.close()

    asyncio.run(scenario())
    (log,) = (data / "launch" / "melonds").glob("*.log")
    assert "Some_Game_Europe_.nds" in log.name
    lines = log.read_text().splitlines()
    assert lines[0].startswith(f"$ {sys.executable} -c")
    assert "out" in lines and "err" in lines
    assert f"dir={data / 'packs' / 'melonds'}" in lines, "a pack is told where its own log goes"


def test_only_the_newest_launches_are_kept(data):
    """By name, which starts with the time: an mtime is moved by any rewrite."""
    folder = data / "launch" / "pcsx2"
    folder.mkdir(parents=True)
    for i in range(logs.LAUNCHES_KEPT + 3):
        (folder / f"20260101-0000{i:02d}-game.log").write_text("x")
    oldest = folder / "20260101-000000-game.log"
    oldest.touch()                      # newest by mtime, oldest by name

    with logs.launch_output("pcsx2", "game.iso", ["pcsx2"]):
        pass

    kept = sorted(p.name for p in folder.glob("*.log"))
    assert len(kept) == logs.LAUNCHES_KEPT
    assert oldest.name not in kept


def test_a_log_growing_past_its_cap_is_emptied_while_it_is_written(data):
    folder = data / "launch" / "rpcs3"
    folder.mkdir(parents=True)
    big, small = folder / "big.log", folder / "small.log"
    with big.open("ab") as writer:                  # the child's append-mode fd
        writer.write(b"a" * (logs.LAUNCH_MAX_BYTES + 1))
        writer.flush()
        small.write_text("fine")
        logs.cap_child_logs()
        writer.write(b"later\n")
        writer.flush()
    assert big.read_bytes() == b"[earlier output cut: over the size cap]\nlater\n"
    assert small.read_text() == "fine"


def test_a_log_that_cannot_be_written_never_costs_the_launch(data, monkeypatch):
    data.parent.mkdir(parents=True)
    data.write_text("a file where the directory should be")
    with logs.launch_output("gb", "game.gb", ["mgba"]) as out:
        assert out is pm.asyncio.subprocess.DEVNULL


def test_a_purge_empties_the_logs_a_running_game_still_writes(data):
    folder = data / "launch" / "gb"
    folder.mkdir(parents=True)
    (folder / "20260101-000000-old.log").write_text("old")
    running = folder / "20260102-000000-now.log"
    pack = data / "packs" / "melonds" / "melonds-multiplayer.log"
    pack.parent.mkdir(parents=True)
    pack.write_text("pack")
    with running.open("ab") as writer:
        writer.write(b"before")
        writer.flush()
        logs.purge()
        writer.write(b"after")
    assert sorted(p.name for p in folder.iterdir()) == [running.name], "the old one goes"
    assert running.read_bytes() == b"after", "the open one is emptied, not unlinked"
    assert pack.read_text() == ""
    assert logs.usage()["files"] == 1


def test_logging_keeps_working_after_a_purge(data):
    logger = logging.getLogger("gamecore.test.logs")
    logger.setLevel(logging.INFO)
    handler = logs.SectionFile(data / "backend" / "backend.log")
    logging.getLogger().addHandler(handler)
    try:
        logger.info("before")
        assert logs.usage()["files"] == 1

        freed = logs.purge()
        assert freed["files"] == 1 and freed["bytes"] > 0
        assert not (data / "backend").exists()

        logger.info("after")
        assert "after" in (data / "backend" / "backend.log").read_text()
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_the_api_reports_and_purges(data):
    (data / "launch" / "gb").mkdir(parents=True)
    (data / "launch" / "gb" / "a.log").write_text("12345")
    app = FastAPI()
    app.include_router(logs_router.router, prefix="/api")
    client = TestClient(app)

    assert client.get("/api/logs").json() == {"files": 1, "bytes": 5}
    assert client.delete("/api/logs").json() == {"ok": True, "freed": {"files": 1, "bytes": 5}}
    assert client.get("/api/logs").json() == {"files": 0, "bytes": 0}


@pytest.fixture
def installed(data):
    """`install()` on the real loggers, undone afterwards."""
    names = [""] + [n for ns in logs.SECTIONS.values() for n in ns]
    levels = {n: logging.getLogger(n).level for n in names}

    def strip():
        # Another test may have run the lifespan, which installs on its own root.
        for h in logs._handlers():
            h.close()
        for n in names:
            lg = logging.getLogger(n)
            lg.handlers = [h for h in lg.handlers if not isinstance(h, logs.SectionFile)]

    strip()
    logs.install()
    yield data
    strip()
    for n in names:
        logging.getLogger(n).setLevel(levels[n])


def test_each_area_writes_its_own_file(installed):
    logging.getLogger("backend.routers.settings.bluetooth").info("paired")
    logging.getLogger("backend.services.configgen.sdl_probe").warning("probe failed")

    network = (installed / "network" / "network.log").read_text()
    controllers = (installed / "controllers" / "controllers.log").read_text()
    backend = (installed / "backend" / "backend.log").read_text()
    assert "paired" in network
    assert "probe failed" in controllers, "a submodule reaches its package's section"
    assert "probe failed" in backend and "paired" not in backend, "backend.log is the warnings"


def test_installing_twice_writes_each_line_once(installed):
    logs.install()
    logging.getLogger("backend.services.launch").info("once")
    assert (installed / "session" / "session.log").read_text().count("once") == 1


def test_every_section_comes_back_after_a_purge(installed):
    logging.getLogger("backend.services.prefetch").info("before")
    logs.purge()
    logging.getLogger("backend.services.prefetch").info("after")
    assert (installed / "media" / "media.log").read_text().strip().endswith("after")


def test_a_mac_is_masked_in_the_middle():
    assert logs.mask_mac("A0:5A:5C:12:34:FF") == "A0:5A:5C:xx:xx:FF"
    assert logs.mask_mac("not a mac") == "xx"


def test_an_interface_error_lands_in_the_ui_log(installed):
    app = FastAPI()
    app.include_router(logs_router.router, prefix="/api")
    client = TestClient(app)

    r = client.post("/api/logs/ui", json={"message": "x is undefined", "source": "index.js:42"})
    assert r.json() == {"ok": True}
    assert "x is undefined (index.js:42)" in (installed / "ui" / "ui.log").read_text()
    client.post("/api/logs/ui", json={"message": "x\n2026-01-01 ERROR forged: line"})
    assert "x\\n2026-01-01 ERROR forged" in (installed / "ui" / "ui.log").read_text(), \
        "a newline in a report cannot forge a record"
    too_long = client.post("/api/logs/ui", json={"message": "a" * (logs.UI_MESSAGE_MAX + 1)})
    assert too_long.status_code == 422


def test_a_record_written_during_a_purge_waits_and_lands_in_a_real_file(installed, monkeypatch):
    """Closed, then a record reopens the file, then the delete removes it: that
    section would log into a deleted file until its rollover."""
    logger = logging.getLogger("backend.services.launch")
    real_empty = logs._empty
    writer = threading.Thread(target=lambda: logger.info("during"))

    def empty_while_someone_logs(root):
        writer.start()
        writer.join(timeout=0.2)
        assert writer.is_alive(), "the record did not wait for the purge"
        real_empty(root)

    monkeypatch.setattr(logs, "_empty", empty_while_someone_logs)
    logs.purge()
    writer.join()
    assert "during" in (installed / "session" / "session.log").read_text()
