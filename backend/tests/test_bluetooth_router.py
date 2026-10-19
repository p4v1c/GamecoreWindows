"""/api/settings/bluetooth — the paired list, and how long it takes to arrive.

**No test here may reach the real adapter.** `bluetoothctl` is stubbed in every
one of them, for the same reason the Wi-Fi suite stubs `nmcli`: a real
`scan on` puts the developer's adapter into discovery for ten seconds, and a
real `disconnect` drops whatever they are listening to.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.main import app                          # noqa: E402

pytestmark = pytest.mark.usefixtures("posix_boundary")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def btctl(monkeypatch):
    """Every `bluetoothctl` this router spawns, answered by argv.

    `answers` maps a fragment of the command to its stdout, so one test can make
    `devices Paired` and `info <mac>` say different things — which the single
    canned stdout the Wi-Fi fixture uses cannot express, and which is the whole
    subject here.
    """
    state = {"calls": [], "answers": {}, "events": []}

    class _Proc:
        returncode = 0

        def __init__(self, argv):
            self.argv = argv

        async def communicate(self, data=None):
            # One yield to the loop before answering. That is what makes the
            # ordering below meaningful: concurrent callers all reach this
            # point before any of them resumes, a sequential one cannot.
            await asyncio.sleep(0)
            if "info" in self.argv:
                state["events"].append(("answered", self.argv[-1]))
            joined = " ".join(self.argv)
            for needle, out in state["answers"].items():
                if needle in joined:
                    return out.encode(), b""
            return b"", b""

        async def wait(self):
            return 0

    async def fake_exec(*argv, **kw):
        state["calls"].append(list(argv))
        if "info" in argv:
            state["events"].append(("spawned", argv[-1]))
        return _Proc(list(argv))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return state


PAIRED = "\n".join([
    "Device E4:17:D8:2A:9C:03 8BitDo Ultimate 2C",
    "Device 5C:BA:11:60:7F:AA DualSense Wireless",
    "Device A0:9E:1B:44:D2:18 Marshall Major IV",
])


def test_the_paired_list_carries_each_device_and_its_connected_state(client, btctl):
    btctl["answers"] = {
        "devices Paired": PAIRED,
        "info E4:17:D8:2A:9C:03": "Connected: yes",
        "info 5C:BA:11:60:7F:AA": "Connected: yes",
        "info A0:9E:1B:44:D2:18": "Connected: no",
    }
    body = client.get("/api/settings/bluetooth/devices").json()
    assert [(d["name"], d["connected"]) for d in body] == [
        ("8BitDo Ultimate 2C", True),
        ("DualSense Wireless", True),
        ("Marshall Major IV", False),
    ]
    assert all(d["paired"] for d in body)


def test_every_info_call_goes_out_before_any_of_them_answers(client, btctl):
    """The paired list used to be a sequential loop — one `bluetoothctl info`
    per device, each awaited before the next was spawned. Three paired devices
    therefore cost three round trips end to end, and the settings screen drew
    its empty state in the meantime: "nothing is paired yet", to somebody
    holding a paired pad.

    Asserted on the ORDER of spawn and answer rather than on a clock. Every
    `info` yields to the loop once before answering, so a concurrent
    implementation spawns all three before the first one comes back and a
    sequential one interleaves them strictly. A stopwatch would have been
    flaky; a gate that blocks until all three arrive would hang the suite on
    the very regression it is meant to catch, which is worse than no test.
    """
    btctl["answers"] = {"devices Paired": PAIRED, "info": "Connected: yes"}

    body = client.get("/api/settings/bluetooth/devices").json()
    assert len(body) == 3

    kinds = [kind for kind, _ in btctl["events"]]
    assert kinds == ["spawned"] * 3 + ["answered"] * 3, (
        "the info calls are serialised — each one is being awaited before the "
        f"next is spawned: {btctl['events']}"
    )


def test_a_box_with_nothing_paired_answers_an_empty_list_not_an_error(client, btctl):
    """The screen distinguishes "nothing paired" from "not loaded yet", so this
    has to be a clean empty list rather than a failure it would render as the
    same thing."""
    btctl["answers"] = {"devices Paired": ""}
    assert client.get("/api/settings/bluetooth/devices").json() == []


# ── Unpair and forget ────────────────────────────────────────────────────────
MAC = "A0:9E:1B:44:D2:18"


def test_forgetting_disconnects_then_removes_the_pairing(client, btctl):
    """The two calls, in that order, on the address the player chose — and
    nothing else touches the adapter."""
    btctl["answers"] = {"devices Paired": "", "remove": "Device has been removed"}
    body = client.delete(f"/api/settings/bluetooth/devices/{MAC}").json()
    assert body["ok"] is True

    verbs = [c[2:] for c in btctl["calls"] if c[:2] == ["bluetoothctl", "--"]]
    assert verbs[:2] == [["disconnect", MAC], ["remove", MAC]]


def test_a_device_still_paired_afterwards_was_not_forgotten(client, btctl):
    """bluetoothctl's exit status is not the answer; the paired list is. A
    removal that left the device paired must say so, or the screen hides a
    device the adapter will happily reconnect."""
    btctl["answers"] = {
        "devices Paired": f"Device {MAC} Marshall Major IV",
        "remove": "Failed to remove device: org.bluez.Error.Failed",
    }
    body = client.delete(f"/api/settings/bluetooth/devices/{MAC}").json()
    assert body["ok"] is False
    assert "Failed to remove device" in body["message"]


def test_an_address_bluez_no_longer_knows_is_already_forgotten(client, btctl):
    btctl["answers"] = {"devices Paired": "", "remove": f"Device {MAC} not available"}
    assert client.delete(f"/api/settings/bluetooth/devices/{MAC}").json()["ok"] is True


def test_a_lower_case_address_is_the_same_device(client, btctl):
    btctl["answers"] = {"devices Paired": f"Device {MAC} Marshall Major IV"}
    body = client.delete(f"/api/settings/bluetooth/devices/{MAC.lower()}").json()
    assert body["ok"] is False


def test_anything_but_an_address_never_reaches_bluetoothctl(client, btctl):
    r = client.delete("/api/settings/bluetooth/devices/--help")
    assert r.status_code == 400
    assert btctl["calls"] == []


def test_named_devices_come_before_bare_addresses_in_both_lists(client, btctl):
    """An unnamed device is listed as its own address, with DASHES — the old
    check stripped colons only and never recognised one."""
    btctl["answers"] = {
        "devices Paired": "\n".join([
            "Device 11:22:33:44:55:66 11-22-33-44-55-66",
            "Device E4:17:D8:2A:9C:03 8BitDo Ultimate 2C",
        ]),
        "devices\n": "",
    }
    body = client.get("/api/settings/bluetooth/devices").json()
    assert [d["name"] for d in body] == ["8BitDo Ultimate 2C", "11-22-33-44-55-66"]

    btctl["answers"] = {
        "devices Paired": "",
        "-- devices": "\n".join([
            "Device AA:BB:CC:DD:EE:01 AA-BB-CC-DD-EE-01",
            "Device AA:BB:CC:DD:EE:02 JBL Flip 5",
            "Device AA:BB:CC:DD:EE:03 AA:BB:CC:DD:EE:03",
            "Device AA:BB:CC:DD:EE:04 Galaxy Buds2",
        ]),
    }
    found = client.post("/api/settings/bluetooth/scan").json()["found"]
    assert [d["name"] for d in found][:2] == ["Galaxy Buds2", "JBL Flip 5"]
    assert {d["name"] for d in found[2:]} == {"AA-BB-CC-DD-EE-01", "AA:BB:CC:DD:EE:03"}


# ── Pairing needs an agent ───────────────────────────────────────────────────

FAKE_BLUETOOTHCTL = r'''#!/usr/bin/env python3
"""bluetoothctl 5.87 as measured: --agent does nothing in direct mode; the
interactive agent asks "(yes/no):" with no newline."""
import sys
if "--" in sys.argv:
    cmd = sys.argv[sys.argv.index("--") + 1]
    if cmd == "pair":
        print("Failed to pair: org.bluez.Error.AuthenticationRejected")
    elif cmd == "connect":
        print("Connection successful")
    sys.exit(0)
agent = any(a.startswith("--agent") for a in sys.argv)   # registered at start-up
if agent:
    print("Agent registered", flush=True)
default = False
for line in sys.stdin:
    word = line.split()
    if not word:
        continue
    if word[0] == "default-agent":
        default = agent; print("Default agent request successful", flush=True)
    elif word[0] == "pair":
        if not default:
            print("Failed to pair: org.bluez.Error.AuthenticationRejected", flush=True)
            continue
        sys.stdout.write("[agent] Confirm passkey 482913 (yes/no): "); sys.stdout.flush()
        answer = sys.stdin.readline().strip()
        print("Pairing successful" if answer == "yes" else "Failed to pair: Rejected", flush=True)
    elif word[0] == "quit":
        break
'''


@pytest.mark.linux_until_port(5)
def test_pairing_registers_an_agent_and_confirms_the_passkey(client, tmp_path, monkeypatch):
    """`bluetoothctl --agent=X -- pair` registers no agent in 5.87: a mouse's
    passkey confirmation and a DualSense's authorization were refused ("No
    agent available") and pairing only worked from the desktop session."""
    fake = tmp_path / "bluetoothctl"
    fake.write_text(FAKE_BLUETOOTHCTL)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    body = client.post("/api/settings/bluetooth/pair", json={"mac": "90:B6:85:FD:61:7D"}).json()
    assert body == {"ok": True, "message": "Paired and connected"}


def test_an_address_with_a_newline_never_reaches_the_pairing_session(client, btctl):
    """The address is typed into an interactive bluetoothctl: "AA…\\nremove X"
    would be a second command."""
    r = client.post("/api/settings/bluetooth/pair", json={"mac": "90:B6:85:FD:61:7D\nremove 4C:B9:9B:C4:AB:B2"})
    assert r.status_code == 400
    assert btctl["calls"] == []
