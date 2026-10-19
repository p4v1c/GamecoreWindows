"""`/api/ready`, and what the shell is allowed to conclude from it.

The Electron shell has to know when GameCore is *usable*, not when its process
exists. It used to ask `/api/sysinfo`, which answers with the box's IP address
— found by opening a UDP socket towards 8.8.8.8 — its disk usage, its
controller batteries and its BIOS inventory. Polled in a loop, during the one
minute when the box has the least to spare, to answer a question it was never
written for.

Two things are asserted here, and the second matters more than the first:

  · the endpoint is cheap and truthful — no socket, no subprocess, no scan;
  · the startup steps are on the right side of the line. A step is required
    when answering without it would make the front end say something false;
    it is a background step when it only refines what is already true.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.main import app                                    # noqa: E402
from backend.services import boot                               # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def test_a_started_box_answers_ready(client):
    with client:
        r = client.get("/api/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True and body["state"] == "ready"


def test_a_box_still_starting_says_so_in_the_status_code(client):
    """503, not 200-with-a-flag: a caller that reads neither this file nor the
    body still gets the answer right, and "not yet" is not an error."""
    with client:
        boot._steps["database"] = boot.PENDING
        try:
            r = client.get("/api/ready")
        finally:
            boot.done("database")
    assert r.status_code == 503
    assert r.json()["ready"] is False
    assert r.json()["steps"]["database"] == "pending"


def test_a_failed_required_step_is_not_ready(client):
    """A box that is broken must not look like a box that is slow."""
    with client:
        boot.failed("database")
        try:
            r = client.get("/api/ready")
        finally:
            boot.done("database")
    assert r.status_code == 503
    assert r.json()["steps"]["database"] == "failed"


def test_the_running_game_adoption_gates_readiness():
    """Answering before it means telling the player nothing is running while an
    emulator is on screen — a home they can walk over a live game."""
    assert "session" in boot.REQUIRED


def test_the_library_scan_does_not_gate_readiness():
    """It walks every system's ROM directory. That is the one startup cost that
    belongs to the size of the player's shelf, and it used to be paid before
    the API answered anything at all."""
    assert "playtime_repair" in boot.BACKGROUND
    assert "playtime_repair" not in boot.REQUIRED


def test_the_screen_does_not_gate_readiness():
    """It talks to X, which at cold boot is the thing most likely not to exist
    yet — for an effect on a screen nobody is looking at."""
    assert "screen" in boot.BACKGROUND and "power" in boot.BACKGROUND


def test_the_answer_costs_a_dict_lookup(client, monkeypatch):
    """Polled every few hundred milliseconds while the box starts. A health
    check that competes with what it measures is not one."""
    import socket
    import subprocess

    def no(*a, **k):
        raise AssertionError("/api/ready did something expensive")

    with client:
        # Patched once the app runs: on Windows the event loop's own self-pipe
        # is a socket pair built with connect(), which is not /api/ready's cost.
        monkeypatch.setattr(socket.socket, "connect", no)
        monkeypatch.setattr(subprocess, "run", no)
        monkeypatch.setattr(subprocess, "Popen", no)
        assert client.get("/api/ready").status_code == 200


def test_sysinfo_is_still_there_for_the_people_who_read_it(client):
    """The expensive endpoint is not the enemy — it is a diagnostic, and it
    keeps its job. What changed is that nothing polls it to decide a boot."""
    with client:
        assert client.get("/api/sysinfo").status_code == 200
