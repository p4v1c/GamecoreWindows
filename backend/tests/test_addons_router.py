"""The addon lifecycle endpoints.

The CLI is never actually run: every test stubs the subprocess, the same way
test_catalog_router.py does. What is asserted is what protects the box — what
may be named, what argv is built, and that two operations cannot overwrite each
other.

An addon install is the one thing on this screen that runs a script and clones
a repository. If it 500s, the Addons screen shows a stack trace where a message
belongs; if the argv drifts, it runs the wrong binary as the wrong user; if two
of them run at once, the second overwrites the first's checkout halfway
through. None of the three is visible from CI without this file.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config import GAMECORE_ROOT    # noqa: E402
from backend.main import app                # noqa: E402
from backend.routers import addons          # noqa: E402


@pytest.fixture
def client():
    # conftest.py provides no application fixture on purpose, so each router
    # test owns its own client and its own reset of the module-level task
    # handle — otherwise a 409 leaks from one test into the next.
    addons._current = None
    return TestClient(app)


@pytest.fixture
def fake_cli(monkeypatch):
    """A CLI that succeeds instantly. Returns the argv list it was handed."""
    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = None

        async def wait(self):
            return 0

        async def communicate(self, _input=None):
            return b"[]", b""

    async def fake_exec(*argv, **kw):
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


# ── an unknown addon answers, it does not blow up ──────────────────────────

@pytest.mark.parametrize("name", [
    "Nope",              # capitals
    "no_underscores",
    "-leading-dash",
    "with space",
    "../../etc/shadow",  # the reason the pattern exists at all
])
def test_a_name_the_registry_cannot_hold_is_refused_cleanly(client, fake_cli, name):
    """400, never 500, and never a subprocess.

    The name lands in an argv, so it is validated before anything is spawned
    rather than after.
    """
    r = client.post(f"/api/addons/{name}/install")
    assert r.status_code in (400, 404), r.text
    assert fake_cli == [], f"a rejected name still spawned {fake_cli}"


def test_an_addon_nobody_has_heard_of_still_gets_a_clean_answer(client, fake_cli):
    """A well-formed name that matches no addon is NOT an error here.

    The registry lives on the box and the CLI is the thing that knows it, so
    the endpoint accepts and reports the failure over the WebSocket. What
    matters is that the HTTP side stays a message, not a traceback.
    """
    r = client.post("/api/addons/definitely-not-an-addon/install")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_a_missing_cli_is_a_503_and_not_a_500(client, monkeypatch):
    """`gamecore-addon list` is the one addon call that answers synchronously.

    On a box where the CLI was never installed — a development checkout, an
    install that stopped early — this must say "unavailable", because a 500
    puts a stack trace on the Addons screen.
    """
    async def boom(*argv, **kw):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    r = client.get("/api/addons/available")
    assert r.status_code == 503, r.text


def test_a_notify_event_that_is_not_one_is_refused(client):
    """Addons reach the TV over this hook, so it takes input from an addon."""
    assert client.post("/api/addons/notify", json={"event": ""}).status_code == 400
    assert client.post("/api/addons/notify",
                       json={"event": "x" * 65}).status_code == 400


# ── the argv, exactly ──────────────────────────────────────────────────────

def test_the_cli_runs_on_the_backends_interpreter_from_the_installation(client, fake_cli):
    """The CLI is a Python script with no extension: Windows cannot run it by name.

    Handed to the backend's own interpreter, it needs no PATH entry and no
    `.cmd` shim (that one is for a person at a prompt).
    """
    assert client.post("/api/addons/rom-manager/install").status_code == 200
    assert fake_cli == [[sys.executable, str(GAMECORE_ROOT / "install" / "bin" / "gamecore-addon"),
                         "install", "rom-manager"]]


@pytest.mark.parametrize("method,url,action", [
    ("post", "/api/addons/rom-manager/install", "install"),
    ("post", "/api/addons/rom-manager/update", "update"),
    ("delete", "/api/addons/rom-manager", "remove"),
])
def test_every_lifecycle_verb_reaches_the_cli_under_its_own_name(
        client, fake_cli, method, url, action):
    assert getattr(client, method)(url).status_code == 200
    assert fake_cli[0][2:] == [action, "rom-manager"]


def test_the_addon_cli_is_not_run_through_sudo(client, fake_cli):
    """Pinned deliberately, and it is the opposite of routers/catalog.py.

    An addon installs its own service and needs no extra privilege: `sudo -n`
    here would make every addon install fail with "a password is required",
    since `-n` never prompts.
    """
    assert client.post("/api/addons/rom-manager/install").status_code == 200
    assert "sudo" not in fake_cli[0], fake_cli[0]


# ── two operations at once ─────────────────────────────────────────────────

def test_a_second_operation_does_not_overwrite_the_first(monkeypatch):
    """One checkout, two writers.

    The busy check is the TASK HANDLE and not the lock, because two requests
    arriving in the same loop tick both see the lock unlocked — the task has
    not started yet — and the second would silently queue instead of being
    refused. Queued is the dangerous answer: the caller is told it succeeded
    and the two runs interleave over the same directory.

    Driven at `_start` rather than over HTTP: TestClient gives each request its
    own event loop, so the first request's task is already dead by the time the
    second arrives and the overlap being tested cannot happen. Under uvicorn
    both live in one loop, which is the situation this reproduces.
    """
    addons._current = None

    async def scenario():
        release = asyncio.Event()

        class _Proc:
            returncode = 0
            stdout = None

            async def wait(self):
                await release.wait()
                return 0

        async def slow_exec(*argv, **kw):
            return _Proc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_exec)

        addons._start("install", "rom-manager")
        await asyncio.sleep(0)          # let the task reach the subprocess
        with pytest.raises(HTTPException) as refused:
            addons._start("install", "other-addon")
        assert refused.value.status_code == 409

        release.set()
        await addons._current

    asyncio.run(scenario())
    addons._current = None


def test_the_next_operation_is_allowed_once_the_first_has_finished(client, fake_cli):
    """The other half: a 409 that never clears is a screen that needs a reboot."""
    assert client.post("/api/addons/rom-manager/install").status_code == 200
    # The handle is done — TestClient has run the task to completion by the
    # time the first response came back with this instant-CLI stub.
    assert client.post("/api/addons/rom-manager/update").status_code == 200
    assert len(fake_cli) == 2


# ── the registry ───────────────────────────────────────────────────────────

def test_a_registry_that_is_not_there_reads_as_empty(client):
    """config/addons.json is excluded from the OTA rsync, so a fresh box has
    none at all. That is "no addons", not a broken screen."""
    r = client.get("/api/addons")
    assert r.status_code == 200
    assert r.json() == []


def test_a_corrupt_registry_reads_as_empty_too(client, monkeypatch, tmp_path):
    """Hand-edited JSON with a trailing comma must not take the screen down."""
    broken = tmp_path / "addons.json"
    broken.write_text("{ oops, ", encoding="utf-8")
    monkeypatch.setattr(addons, "ADDONS_FILE", broken)
    assert client.get("/api/addons").json() == []
