"""The OTA endpoint: one update at a time, and a timeout that really stops it.

Nothing here downloads or installs anything: update/windows.py is replaced by
a stand-in script in a temp directory.

Run under pytest:  pytest backend/tests/test_update.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psutil
import pytest

from backend.routers import update as update_router
from backend.services.process_manager import kill_process_group


@pytest.fixture
def fake_script(tmp_path, monkeypatch):
    """Point the endpoint at a script we control instead of update/windows.py."""
    root = tmp_path / "gc"
    (root / "update").mkdir(parents=True)

    def install(body: str) -> Path:
        p = root / "update" / "windows.py"
        p.write_text("import sys, time\n" + body, encoding="utf-8", newline="\n")
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

def test_a_second_apply_is_refused_with_409(client, fake_script):
    """The Linux updater wiped its work directory on entry, under the first
    run's copy. Nothing stopped a second run: the UI's `installing` flag was
    component state, so leaving the page and returning re-enabled the button.
    """
    # Short enough that the stand-in is gone by the end of the run either way.
    fake_script("time.sleep(3)\n")

    assert client.get("/api/update/status").json() == {"running": False}

    assert client.post("/api/update/apply").status_code == 200
    time.sleep(0.5)

    assert client.get("/api/update/status").json() == {"running": True}
    r = client.post("/api/update/apply")
    assert r.status_code == 409, r.text
    assert "already running" in r.json()["detail"]


def test_apply_is_allowed_again_once_the_previous_run_finished(client, fake_script):
    fake_script("sys.exit(0)\n")

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


def test_stage_one_runs_with_the_backend_interpreter(client, fake_script, tmp_path):
    """The installation's venv runs update/windows.py install; no bash, no PATH lookup."""
    seen = tmp_path / "argv.txt"
    fake_script(f"open(r'{seen}', 'w').write(sys.executable + '|' + ' '.join(sys.argv[1:]))\n")
    assert client.post("/api/update/apply").status_code == 200
    for _ in range(100):
        if seen.exists() and seen.read_text():
            break
        time.sleep(0.05)
    interpreter, args = seen.read_text().split("|")
    assert Path(interpreter).resolve() == Path(sys.executable).resolve()
    assert args == "install"


# ── #20 · the timeout has to take the children too ───────────────────────────

def test_killing_stage_one_takes_its_children_with_it(tmp_path):
    """The Linux timeout killed bash only: its copies carried on writing into
    the install after the backend had told the UI the update was aborted."""
    marker = tmp_path / "child.pid"
    script = tmp_path / "spawner.py"
    script.write_text(
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{marker}', 'w').write(str(child.pid))\n"
        "child.wait()\n", encoding="utf-8")

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        for _ in range(200):                 # wait for the child to be recorded
            if marker.exists() and marker.read_text().strip():
                break
            await asyncio.sleep(0.05)
        child_pid = int(marker.read_text().strip())
        await kill_process_group(proc)
        await proc.wait()
        return child_pid

    child_pid = asyncio.run(scenario())
    for _ in range(100):
        if not psutil.pid_exists(child_pid):
            break
        time.sleep(0.05)
    else:
        psutil.Process(child_pid).kill()
        pytest.fail(f"child {child_pid} survived the tree kill")


def test_kill_process_group_is_safe_on_an_already_dead_process():
    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "pass", stdout=asyncio.subprocess.DEVNULL)
        await proc.wait()
        await kill_process_group(proc)        # must not raise
        await kill_process_group(None)

    asyncio.run(scenario())


if __name__ == "__main__":
    print("Run this one under pytest — it uses tmp_path and monkeypatch fixtures:")
    print("    pytest backend/tests/test_update.py")
    sys.exit(0)
