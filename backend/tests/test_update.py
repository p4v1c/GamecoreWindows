"""The OTA endpoint: one update at a time, and a timeout that really stops it.

Nothing here downloads or installs anything — update/linux.sh is replaced by a
stand-in script in a temp directory.

Run under pytest:  pytest backend/tests/test_update.py
"""
import asyncio
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.routers import update as update_router
from backend.services.process_manager import kill_process_group


@pytest.fixture
def fake_script(tmp_path, monkeypatch):
    """Point the endpoint at a script we control instead of update/linux.sh."""
    root = tmp_path / "gc"
    (root / "update").mkdir(parents=True)

    def install(body: str) -> Path:
        p = root / "update" / "linux.sh"
        p.write_text("#!/usr/bin/env bash\n" + body)
        p.chmod(0o755)
        return p

    monkeypatch.setattr(update_router, "GAMECORE_ROOT", root)
    update_router._current = None
    yield install
    update_router._current = None


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend import main
    with TestClient(main.app) as c:
        yield c


# ── #19 · one at a time ──────────────────────────────────────────────────────

@pytest.mark.linux_until_port(8)
def test_a_second_apply_is_refused_with_409(client, fake_script):
    """update/linux.sh wipes its work directory on entry and rsyncs from it.

    A second run therefore ran `rm -rf` underneath the first one's rsync into
    GAMECORE_PATH. Nothing stopped it: the UI's `installing` flag was component
    state, so leaving the page and returning re-enabled the button.
    """
    # Short enough that the stand-in is gone by the end of the run either way.
    fake_script("sleep 3\n")

    assert client.get("/api/update/status").json() == {"running": False}

    assert client.post("/api/update/apply").status_code == 200
    time.sleep(0.5)

    assert client.get("/api/update/status").json() == {"running": True}
    r = client.post("/api/update/apply")
    assert r.status_code == 409, r.text
    assert "already running" in r.json()["detail"]


@pytest.mark.linux_until_port(8)
def test_apply_is_allowed_again_once_the_previous_run_finished(client, fake_script):
    fake_script("exit 0\n")

    assert client.post("/api/update/apply").status_code == 200
    for _ in range(50):
        if not client.get("/api/update/status").json()["running"]:
            break
        time.sleep(0.1)
    assert client.get("/api/update/status").json() == {"running": False}
    assert client.post("/api/update/apply").status_code == 200


def test_a_missing_script_is_404_not_a_stuck_lock(client, fake_script):
    # fake_script's fixture created the directory but we never write the file.
    r = client.post("/api/update/apply")
    assert r.status_code == 404
    assert client.get("/api/update/status").json() == {"running": False}


# ── #20 · the timeout has to take the children too ───────────────────────────

@pytest.mark.linux_until_port(8)
def test_killing_the_shell_takes_its_children_with_it(tmp_path):
    """The timeout killed bash only — rsync, pip and npm carried on writing
    into /opt/GameCore after the backend had told the UI it was aborted."""
    marker = tmp_path / "child.pid"
    script = tmp_path / "spawner.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"sleep 120 & echo $! > {marker}\n"
        "wait\n"
    )
    script.chmod(0o755)

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            "bash", str(script),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,          # what apply_update now passes
        )
        for _ in range(100):                 # wait for the child to be recorded
            if marker.exists() and marker.read_text().strip():
                break
            await asyncio.sleep(0.05)
        child_pid = int(marker.read_text().strip())

        await kill_process_group(proc)
        await proc.wait()
        return child_pid

    child_pid = asyncio.run(scenario())

    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except (ProcessLookupError, PermissionError):
            break
        time.sleep(0.05)
    else:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except OSError:
            pass
        pytest.fail(f"child {child_pid} survived the group kill")


def test_kill_process_group_is_safe_on_an_already_dead_process():
    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            "true", stdout=asyncio.subprocess.DEVNULL, start_new_session=True)
        await proc.wait()
        await kill_process_group(proc)        # must not raise
        await kill_process_group(None)

    asyncio.run(scenario())


# ── #18 · VERSION is written after everything that can fail ──────────────────

def test_version_is_written_after_pip_and_the_build():
    """A failure in between used to leave VERSION claiming the new release, so
    /api/update/check reported the box up to date and the retry was gone."""
    script = (Path(__file__).resolve().parents[2] / "update/linux.sh").read_text()
    version_write = script.index('> "${GAMECORE_PATH}/VERSION"')
    pip_install = script.index("/.venv/bin/pip")
    frontend = script.index("Rebuilding frontend")
    restart = script.index("Scheduling service restart")

    assert pip_install < version_write, "VERSION must come after pip install"
    assert frontend < version_write, "VERSION must come after the frontend build"
    assert version_write < restart, "and still before the restart is scheduled"


def test_the_update_script_locks_and_uses_a_private_work_directory():
    script = (Path(__file__).resolve().parents[2] / "update/linux.sh").read_text()
    assert "flock -n 9" in script, "no lock — two runs could overlap"
    assert "mktemp -d" in script, "a fixed TMP_DIR is shared between runs"
    assert "rm -rf \"$TMP_DIR\"\nmkdir" not in script, "the destructive wipe on entry is gone"


if __name__ == "__main__":
    print("Run this one under pytest — it uses tmp_path and monkeypatch fixtures:")
    print("    pytest backend/tests/test_update.py")
    sys.exit(0)
