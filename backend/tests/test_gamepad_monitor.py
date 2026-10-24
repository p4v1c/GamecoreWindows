"""Unit tests for the gamepad detector (services.gamepad_monitor).

Two failures that both look like "the emulator is broken" from the sofa:
a pad with no Home button never being detected at all, and a Bluetooth pad
losing its player slot when it reconnects.

SDL's snapshot (`platform/gamepads.py` `pads()`) is replaced with a list the
test controls, so these run without a controller plugged in.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from backend.services import gamepad_monitor as gm
from backend.services import gamepad_devices as gdev
from backend.services.platform import gamepads as platform_gamepads

# Read at import, before conftest's session guard keeps SDL from starting.
REAL_START = platform_gamepads.start


def pad(device="sdl0", name="PS4 Controller", serial="", vendor="054c", product="09cc",
        bus=0x05, is_pad=True, has_guide=True):
    return platform_gamepads.PadInfo(
        device=device, name=name, vendor=vendor, product=product,
        guid="05000000" + "0" * 24, serial=serial, bus=bus, is_pad=is_pad,
        has_guide=has_guide, counts=(16, 6, 1), rest=(0,) * 6, controller=is_pad)


@pytest.fixture
def fake_pads(monkeypatch):
    """SDL's snapshot, as a list the test fills."""
    pads: list = []
    monkeypatch.setattr(platform_gamepads, "pads", lambda: list(pads))
    gdev._logged_no_guide.clear()
    return pads


# ── #11 · detection ──────────────────────────────────────────────────────────

def test_a_pad_with_a_guide_button_is_detected(fake_pads):
    fake_pads.append(pad("sdl3", serial="84:30:95:07:c8:1c"))
    assert gm._find_gamepad_devices() == {
        "sdl3": ("PS4 Controller", "84:30:95:07:c8:1c", True, "054c", "09cc", 0x05)}


def test_a_pad_without_a_guide_button_is_detected_too(fake_pads):
    """A generic pad, an arcade stick, an 8BitDo in DInput: no Home button,
    still a player."""
    fake_pads.append(pad("sdl5", name="USB Gamepad", vendor="0079", product="0006",
                         bus=0x03, has_guide=False))
    found = gm._find_gamepad_devices()
    assert found["sdl5"][2] is True, "a pad without a Guide button must still take a slot"


def test_a_wheel_is_watched_but_takes_no_player_slot(fake_pads):
    fake_pads.append(pad("sdl1", name="Racing Wheel", is_pad=False, has_guide=False))
    found = gm._find_gamepad_devices()
    assert "sdl1" in found, "still watched: it wakes the box"
    assert gm.pads_by_key(found) == {}


def test_the_no_guide_notice_is_logged_once_per_device(fake_pads, caplog):
    fake_pads.append(pad("sdl5", name="USB Gamepad", has_guide=False))
    with caplog.at_level(logging.INFO, logger=gdev.log.name):
        gm._find_gamepad_devices()
        gm._find_gamepad_devices()
        gm._find_gamepad_devices()
    notices = [r for r in caplog.records if "no Guide button" in r.getMessage()]
    assert len(notices) == 1, f"scan runs every few seconds; logged {len(notices)}x"


def test_a_pc_with_no_controller_stays_silent(fake_pads, caplog):
    with caplog.at_level(logging.DEBUG, logger=gdev.log.name):
        assert gm._find_gamepad_devices() == {}
    assert caplog.records == []


def test_a_bluetooth_address_keeps_the_slot_across_reconnections(fake_pads):
    """SDL gives the pad a new instance (`sdl7`) on every reconnection; the
    registry key is its address, so the slot is the same."""
    fake_pads.append(pad("sdl3", serial="84:30:95:07:c8:1c"))
    first = gm.pads_by_key(gm._find_gamepad_devices())
    fake_pads[:] = [pad("sdl7", serial="84:30:95:07:c8:1c")]
    assert gm.pads_by_key(gm._find_gamepad_devices()).keys() == first.keys()


@pytest.mark.parametrize("raw, serial", [("40-1b-5f-b9-ea-8d", "40:1b:5f:b9:ea:8d"),
                                         ("401B5FB9EA8D", "40:1b:5f:b9:ea:8d"),
                                         ("", ""), ("ABC123", "ABC123")])
def test_a_serial_that_is_an_address_is_written_as_one(raw, serial):
    assert platform_gamepads.normalize_serial(raw) == serial


@pytest.mark.parametrize("guid, bus", [("05000000" + "0" * 24, 0x05),
                                       ("03000000" + "0" * 24, 0x03), ("zz", 0)])
def test_the_bus_is_read_from_the_guid(guid, bus):
    assert platform_gamepads.bus_of(guid) == bus


@pytest.mark.parametrize("kind, buttons, controller, padlike", [
    (1, 16, True, True), (0, 10, False, True), (0, 2, False, False), (2, 20, False, False)])
def test_wheels_and_two_button_devices_are_not_players(kind, buttons, controller, padlike):
    assert platform_gamepads.is_padlike(kind, buttons, controller) is padlike


# ── #12 · one pad, several device nodes ──────────────────────────────────────
#
# key_for() returns the pad's MAC, so a pad SDL lists twice under one address
# maps two devices to a single registry key. These used to assert on a dict literal that mirrored the monitor's own
# condition, so deleting that condition would have left them all green. They
# now go through pads_by_key() and _reconcile(), which is where the decision
# actually lives.

import asyncio

from backend.services import controller_profiles as cp
from backend.services import controller_registry as reg


def _node(path, mac, vendor="054c", product="09cc", name="Wireless Controller",
          bustype=0x05):
    return {path: (name, mac, True, vendor, product, bustype)}


class _FakeWS:
    async def broadcast(self, *_a, **_k):
        pass


def _make_roster(monkeypatch):
    """A recorder around the profiling calls, with the registry reset.

    Split out of the fixture so the `python backend/tests/…` runner at the
    bottom can use it too."""
    calls: list[tuple] = []
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: {
        "054c:09cc": "PS4 Controller", "045e:02fd": "Xbox One Controller",
    }.get(f"{v}:{p}", n))
    monkeypatch.setattr(cp, "apply_profile",
                        lambda pl, v, p, n, d: calls.append(("apply", pl, v, d)) or ["ok"])
    # The occupied roster is recorded, not just the slot: it is the argument
    # that lets a release decide anything the roster owns rather than the slot
    # (the PS1/PS2 multitap), and a stub that swallowed it would let the
    # monitor stop passing it without a single test noticing.
    monkeypatch.setattr(cp, "release_profile",
                        lambda pl, occ=(): calls.append(
                            ("release", pl, tuple(sorted(occ)))) or ["released"])

    state: dict = {"live": {}, "applied": {}}

    def scan(devices):
        was = state["live"]
        state["live"] = gm.pads_by_key(devices)
        calls.clear()
        if was != state["live"]:
            asyncio.run(gm._reconcile(was, state["live"], state["applied"], False, _FakeWS()))
        return list(calls)

    return scan


def _applies(calls):
    return [c for c in calls if c[0] == "apply"]


def _releases(calls):
    """{slot: the roster that was still occupied when it was freed}.

    Every scan now sweeps every slot no pad holds, so the interesting question
    stopped being "was a release emitted" and became "which slots, and what was
    it told about the rest".
    """
    return {c[1]: c[2] for c in calls if c[0] == "release"}


@pytest.fixture
def roster(monkeypatch):
    try:
        yield _make_roster(monkeypatch)
    finally:
        reg._slots.clear(); reg._labels.clear()


def test_a_pad_with_several_nodes_takes_one_slot(roster):
    """A DualShock 4 exposes a touchpad and a motion node besides its buttons."""
    pad = {**_node("sdl20", "84:30:95:07:c8:1c"),
           **_node("sdl21", "84:30:95:07:c8:1c", name="Touchpad"),
           **_node("sdl22", "84:30:95:07:c8:1c", name="Motion Sensors")}

    calls = roster(pad)
    assert _applies(calls) == [("apply", 1, "054c", 0)]
    # The three slots nobody holds are swept on the same pass — that is what
    # closes the reboot hole, and it must happen even when nothing left.
    assert _releases(calls) == {2: (1,), 3: (1,), 4: (1,)}
    assert reg.snapshot() == [{"player": 1, "label": "Wireless Controller"}]


def test_a_pad_keeps_its_slot_while_another_node_is_alive(roster):
    """Unplugging the charging cable of a Bluetooth pad is not a disconnect."""
    bt = _node("sdl20", "84:30:95:07:c8:1c")
    roster({**bt, **_node("sdl24", "84:30:95:07:c8:1c")})

    assert roster(bt) == [], "the Bluetooth node is still there — nothing happens"
    assert reg.snapshot() == [{"player": 1, "label": "Wireless Controller"}]


def test_the_slot_is_released_once_the_last_node_goes(roster):
    roster(_node("sdl20", "84:30:95:07:c8:1c"))

    calls = roster({})
    assert _applies(calls) == []
    # Nobody is left, so every slot is freed and every one of them is told the
    # roster is empty. That last part is what turns the multitap back off.
    assert _releases(calls) == {1: (), 2: (), 3: (), 4: ()}
    assert reg.snapshot() == []


def test_unplugging_one_pad_does_not_touch_another(roster):
    p1 = _node("sdl20", "84:30:95:07:c8:1c")
    p2 = _node("sdl21", "aa:bb:cc:dd:ee:ff", "045e", "02fd", "Xbox Wireless")
    roster({**p1, **p2})

    calls = roster(p1)
    assert _releases(calls) == {2: (1,), 3: (1,), 4: (1,)}
    assert 1 not in _releases(calls), "the surviving pad's slot must not be freed"
    assert reg.snapshot() == [{"player": 1, "label": "Wireless Controller"}]


# ── the survivors are re-profiled when the roster changes ────────────────────

def test_the_survivor_of_two_identical_pads_is_re_profiled(roster):
    """dup is relative to the roster, so a departure invalidates what is left.

    Two DualShock 4s: the second is written as `SDL/1/PS4 Controller` for
    Dolphin and `PS4 Controller 2` for RPCS3. When the first pad's battery
    dies, those strings stop describing anything — the survivor must go back
    to dup 0. Only the arriving pad used to be profiled, so it never did.

    The survivor also moves back to player 1: slots are compacted between
    games, so the pad left alone is the one the game asks for first. It used
    to keep slot 2 for the rest of the session, which left Ryujinx presenting
    no Player 1 at all.
    """
    a = _node("sdl20", "84:30:95:07:c8:1c")
    b = _node("sdl21", "aa:bb:cc:dd:ee:ff")

    assert _applies(roster(a)) == [("apply", 1, "054c", 0)]
    assert _applies(roster({**a, **b})) == [("apply", 2, "054c", 1)]
    calls = roster(b)
    assert _applies(calls) == [("apply", 1, "054c", 0)]
    # Slot 2 is freed because the survivor compacted into slot 1, not because
    # its own pad left — the sweep reads the roster, so it gets that right
    # where hanging the release off the departure event did not.
    assert _releases(calls) == {2: (1,), 3: (1,), 4: (1,)}


def test_an_unchanged_roster_writes_nothing(roster):
    """Idempotence: the scan runs every three seconds for the whole session."""
    pad = _node("sdl20", "84:30:95:07:c8:1c")
    roster(pad)
    assert roster(pad) == []
    assert roster(pad) == []


def test_a_pad_whose_ids_are_still_zero_takes_no_slot(roster):
    """uhid can expose a Bluetooth pad before the kernel fills its ids in.

    Such a pad used to be given a player slot and then refused a config, and
    `known` blocked every later attempt — so it held that slot, unconfigured,
    until the backend restarted.
    """
    ghost = {"sdl9": ("uhid pad", "", True, "0000", "0000", 0x05)}

    assert roster(ghost) == []
    assert reg.snapshot() == []

    real = {"sdl9": ("8BitDo Pro 2", "", True, "2dc8", "6003", 0x05)}
    assert _applies(roster(real)) == [("apply", 1, "2dc8", 0)]


def test_dup_counts_by_resolved_name_not_by_vendor_product():
    """Every consumer of dup counts by name: Dolphin writes SDL/<dup>/<name>
    and RPCS3 `<name> <dup+1>`. SDL3_FALLBACK_NAMES alone maps 054c:05c4,
    054c:09cc and 054c:0ba0 onto "PS4 Controller", so counting by
    vendor:product gave two different DualShock 4 revisions dup 0 each — one
    pad drove two ports and the other was dead.
    """
    assert gm.dup_indexes({
        "a": (1, "PS4 Controller"),
        "b": (2, "PS4 Controller"),
        "c": (3, "Xbox One Controller"),
    }) == {"a": 0, "b": 1, "c": 0}


def test_devices_sort_by_number_not_lexicographically():
    """Slots are handed out in this order on the first scan, so `sdl10`
    sorting before `sdl2` would make the numbering depend on string order."""
    assert sorted(["sdl10", "sdl2", "sdl20"], key=gm._event_sort_key) == [
        "sdl2", "sdl10", "sdl20"]


# ── a failed profiling pass must be retried ──────────────────────────────────
# apply_profile logged its give-ups and returned only the successes, so the
# monitor could not tell a transient failure from a clean pass — and it marked
# the pad done *before* the attempt. A DualShock 4's Ryujinx slot went missing
# for good that way: SDL had not yet caught up with a fresh Bluetooth
# connection, _ryujinx refused to invent an id, and the pad was never revisited.

def _one_pad():
    return {**_node("sdl20", "aa:bb:cc:dd:ee:ff")}


def _run_reconcile(applied, live, ws):
    asyncio.run(gm._reconcile({}, live, applied, False, ws))


def test_an_incomplete_pass_is_retried(monkeypatch):
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: "PS4 Controller")
    attempts = []

    def flaky(pl, v, p, n, d):
        attempts.append(pl)
        # Fails while SDL has not caught up, then succeeds — the real case.
        if len(attempts) < 3:
            return cp.ProfileResult([], ["ryujinx: SDL2 would not report a GUID"])
        return cp.ProfileResult(["ryujinx: Player 1 created"])

    monkeypatch.setattr(cp, "apply_profile", flaky)
    live = gm.pads_by_key(_one_pad())
    applied: dict = {}
    for _ in range(5):
        _run_reconcile(applied, live, _FakeWS())

    assert len(attempts) == 3, "a give-up was remembered as a success"
    # Settled once it worked: no further attempts, however many scans go by.
    _run_reconcile(applied, live, _FakeWS())
    assert len(attempts) == 3


def test_a_clean_pass_is_not_repeated(monkeypatch):
    """The whole point of `applied` — one write per pad, not one every 3 s."""
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: "PS4 Controller")
    attempts = []
    monkeypatch.setattr(cp, "apply_profile",
                        lambda pl, v, p, n, d: attempts.append(pl) or cp.ProfileResult(["ok"]))
    live = gm.pads_by_key(_one_pad())
    applied: dict = {}
    for _ in range(4):
        _run_reconcile(applied, live, _FakeWS())
    assert len(attempts) == 1


def test_retrying_stops_instead_of_spinning_forever(monkeypatch):
    """Some give-ups are permanent, and each retry pays for SDL probes with an
    8 s timeout. The budget has to run out."""
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: "PS4 Controller")
    attempts = []
    monkeypatch.setattr(cp, "apply_profile",
                        lambda pl, v, p, n, d: attempts.append(pl) or
                        cp.ProfileResult([], ["rpcs3: nothing to clone from"]))
    live = gm.pads_by_key(_one_pad())
    applied: dict = {}
    for _ in range(20):
        _run_reconcile(applied, live, _FakeWS())
    assert len(attempts) == gm.PROFILE_RETRIES


def test_a_reconnection_gives_a_fresh_budget(monkeypatch):
    """Unplugging and replugging is what an owner does when something is wrong;
    it must actually mean something."""
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: "PS4 Controller")
    attempts = []
    monkeypatch.setattr(cp, "apply_profile",
                        lambda pl, v, p, n, d: attempts.append(pl) or
                        cp.ProfileResult([], ["ryujinx: no GUID"]))
    live = gm.pads_by_key(_one_pad())
    applied: dict = {}
    for _ in range(10):
        _run_reconcile(applied, live, _FakeWS())
    assert len(attempts) == gm.PROFILE_RETRIES

    applied.clear()          # what a disconnect/reconnect amounts to
    reg._slots.clear(); reg._labels.clear()
    for _ in range(10):
        _run_reconcile(applied, live, _FakeWS())
    assert len(attempts) == gm.PROFILE_RETRIES * 2


# ── transport, and closing up the slots ──────────────────────────────────────

def test_moving_a_pad_from_bluetooth_to_usb_re_profiles_it(roster):
    """An SDL GUID encodes the bus, and Ryujinx binds by that GUID.

    The MAC, the vendor and the product are all identical across a transport
    change, so without the bus in the footprint the pad looked unchanged and
    every GUID-bound emulator kept pointing at a device that no longer exists.
    """
    bt  = _node("sdl20", "84:30:95:07:c8:1c", bustype=0x05)
    usb = _node("sdl20", "84:30:95:07:c8:1c", bustype=0x03)

    assert _applies(roster(bt)) == [("apply", 1, "054c", 0)]
    assert _applies(roster(usb)) == [("apply", 1, "054c", 0)], \
        "a cable is a different device to SDL"


def test_the_last_pad_left_becomes_player_one(roster):
    """Unplug player 1 in co-op and the survivor used to keep slot 2 for good —
    so Ryujinx presented no Player 1 at all to a game that wanted one."""
    a = _node("sdl20", "84:30:95:07:c8:1c")
    b = _node("sdl21", "aa:bb:cc:dd:ee:ff", vendor="045e", product="02fd")

    roster(a)
    roster({**a, **b})
    assert reg._slots[gm.controller_registry.key_for("aa:bb:cc:dd:ee:ff", "")] == 2

    roster(b)                       # player 1 leaves
    assert reg._slots[gm.controller_registry.key_for("aa:bb:cc:dd:ee:ff", "")] == 1


def test_slots_are_not_renumbered_while_a_game_runs(roster, monkeypatch):
    """Closing the gap mid-session would silently make player 2 into player 1."""
    monkeypatch.setattr(gm, "_game_running", lambda: True)
    a = _node("sdl20", "84:30:95:07:c8:1c")
    b = _node("sdl21", "aa:bb:cc:dd:ee:ff", vendor="045e", product="02fd")

    roster(a)
    roster({**a, **b})
    roster(b)
    assert reg._slots[gm.controller_registry.key_for("aa:bb:cc:dd:ee:ff", "")] == 2


def test_slots_are_not_renumbered_while_a_game_is_only_suspended(roster,
                                                                 monkeypatch):
    """A suspended game is a session in progress, and renumbering under it costs
    the same thing it costs under a running one — just later.

    The reasoning that said otherwise stopped one step short: a frozen emulator
    re-reads no input config, true, so nothing goes wrong *while* it is frozen.
    It goes wrong on RESUME. The emulator comes back holding the binding it read
    at startup, the registry has since made player 2 into player 1, and the
    surviving pad now writes to a slot the game is not listening on.
    """
    from backend.services import process_manager as pm
    from backend.services.game_session import Session
    from backend.tests.fake_jobs import FakeJob
    manager = pm.ProcessManager()
    manager._sessions.append(Session(
        job=FakeJob(111),
        game_key="held.iso", system_id="dolphin", state="background",
        session_id=1))
    monkeypatch.setattr(pm, "process_manager", manager)

    a = _node("sdl20", "84:30:95:07:c8:1c")
    b = _node("sdl21", "aa:bb:cc:dd:ee:ff", vendor="045e",
              product="02fd")
    roster(a)
    roster({**a, **b})
    roster(b)                       # player 1 leaves while the game is frozen

    assert reg._slots[gm.controller_registry.key_for("aa:bb:cc:dd:ee:ff", "")] == 2


# ── SDL missing: the failure that looks exactly like "no pad" ────────────────

def test_a_pc_where_sdl_cannot_run_says_so_and_waits_for_nothing(monkeypatch, caplog):
    """No SDL2.dll: no pad will ever be seen. Said once, loudly, and a launch
    must not wait for a profiling pass that will never come."""
    import asyncio

    monkeypatch.setattr(platform_gamepads, "start", lambda: False)
    monkeypatch.setattr(platform_gamepads, "unavailable", lambda: "SDL2.dll not found")
    with caplog.at_level(logging.ERROR, logger=gm.log.name):
        asyncio.run(gm.run())
    assert any("SDL2.dll" in r.getMessage() for r in caplog.records)
    assert gm.unprofiled() == []


# ── the guide button, read from the pad layer ────────────────────────────────

def _watch(monkeypatch, events):
    """Run `_watch_device` over a script of pad-layer events; return the gestures."""
    import asyncio

    fired: list[str] = []

    async def suspend(gesture, foreground_only=False, from_game=False):
        fired.append(gesture)

    def subscribe(device):
        queue = asyncio.Queue()
        for event in [*events, platform_gamepads.GONE]:
            queue.put_nowait(event)
        return queue

    monkeypatch.setattr(platform_gamepads, "subscribe", subscribe)
    monkeypatch.setattr(platform_gamepads, "unsubscribe", lambda device, queue: None)
    monkeypatch.setattr(gm, "_suspend_to_interface", suspend)
    monkeypatch.setattr(gm, "_last_guide_press", None)
    monkeypatch.setattr(gm, "_guide_armed", False)
    # Presses 0.3 s apart: past the debounce, inside the double-press window.
    # Only the monitor's clock: asyncio's own must stay real.
    import types
    clock = iter(i * 0.3 for i in range(1000))
    monkeypatch.setattr(gm, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    from backend.services import standby
    monkeypatch.setattr(standby, "on_input", lambda: None)
    asyncio.run(gm._watch_device("sdl0"))
    return fired


def test_a_guide_double_press_from_sdl_reaches_the_gesture(monkeypatch):
    from backend.services import process_manager as pm
    monkeypatch.setattr(type(pm.process_manager), "is_foreground", property(lambda self: False))
    guide = platform_gamepads.controller_button(5, True)
    assert guide == platform_gamepads.PadEvent(gdev.EV_KEY, gdev.BTN_MODE, 1, "pad")
    assert _watch(monkeypatch, [guide, platform_gamepads.controller_button(5, False),
                                guide]) == ["guide"]


def test_the_raw_layer_never_triggers_the_guide(monkeypatch):
    raw_guide = platform_gamepads.PadEvent(gdev.EV_KEY, gdev.BTN_MODE, 1, "raw")
    assert _watch(monkeypatch, [raw_guide, raw_guide]) == []


def test_the_dpad_buttons_arrive_as_a_hat():
    assert platform_gamepads.controller_button(11, True) == \
        platform_gamepads.PadEvent(gdev.EV_ABS, 0x11, -1, "pad")
    assert platform_gamepads.controller_button(14, False) == \
        platform_gamepads.PadEvent(gdev.EV_ABS, 0x10, 0, "pad")


def test_a_hat_is_split_into_two_signed_axes():
    up_right = platform_gamepads.hat_events(0, 3, "raw")
    assert [(e.code, e.value) for e in up_right] == [(0x10, 1), (0x11, -1)]
    assert [(e.code, e.value) for e in platform_gamepads.hat_events(1, 0, "raw")] == \
        [(0x12, 0), (0x13, 0)]


# ── the give-up reaches the player, not just the journal ─────────────────────
#
# P1 made "this pad cannot be named" visible in the log. That is not where
# the player is standing: they have just plugged a controller in and it does
# not work. The connect toast is, and it said "Controller 2 connected" in
# green for a pad dead in every emulator that matches by name.


class _RecordingWS:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def broadcast(self, event, data=None):
        self.sent.append((event, data or {}))


def _connect_event(monkeypatch, identified: bool):
    """Plug one pad in and return the gp:connected payload."""
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: n)
    monkeypatch.setattr(cp, "apply_profile", lambda *a, **k: ["ok"])
    monkeypatch.setattr(cp, "release_profile", lambda *a, **k: [])
    monkeypatch.setattr(cp, "identification",
                        lambda v, p, n: {"identified": identified})

    live = gm.pads_by_key(_node("sdl9", "84:30:95:07:c8:1c",
                                vendor="1d79", product="0f0f",
                                name="Generic USB Gamepad"))
    ws = _RecordingWS()
    asyncio.run(gm._reconcile({}, live, {}, False, ws))
    return next(d for e, d in ws.sent if e == "gp:connected")


def test_an_unnameable_pad_is_flagged_on_the_connect_event(monkeypatch):
    """Without this the toast has nothing to decide on and the only trace of a
    dead controller is a line in a log nobody reads from a sofa."""
    payload = _connect_event(monkeypatch, identified=False)

    assert payload["unmapped"] is True, payload
    # The wizard needs to know WHICH pad, and the toast shows its name.
    assert payload["vendor"] == "1d79" and payload["product"] == "0f0f"
    assert payload["label"] == "Generic USB Gamepad"


def test_a_pad_the_box_can_name_is_not_flagged(monkeypatch):
    """The flag has to mean something. If it were always true the first person
    to see the offer would learn to ignore it — which is exactly what happened
    to the give-up when it only went to the journal."""
    payload = _connect_event(monkeypatch, identified=True)

    assert payload["unmapped"] is False, payload


def test_a_broken_identification_does_not_lose_the_connect_event(monkeypatch):
    """A pad arriving is news whatever we can work out about it. Letting this
    question raise would take the toast down with it — and the pad that most
    needs announcing is the one we understand least."""
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: n)
    monkeypatch.setattr(cp, "apply_profile", lambda *a, **k: ["ok"])
    monkeypatch.setattr(cp, "release_profile", lambda *a, **k: [])

    def boom(*_a, **_k):
        raise RuntimeError("SDL fell over")

    monkeypatch.setattr(cp, "identification", boom)

    live = gm.pads_by_key(_node("sdl9", "84:30:95:07:c8:1c"))
    ws = _RecordingWS()
    asyncio.run(gm._reconcile({}, live, {}, False, ws))

    assert any(e == "gp:connected" for e, _ in ws.sent)


def test_the_arrival_toast_names_the_systems_left_unconfigured(monkeypatch):
    """A recognised pad that one emulator refused must say WHICH one.

    The reference box's Xbox pad was identified fine, so `unmapped` was false
    and the arrival toast was the green "Controller 1 connected" — while
    Ryujinx had been skipped and the Switch went on running an old mapping.
    `unmapped` cannot carry this: it answers "can this pad be named", which
    was true. Nothing else reached the player, and a journal line is not
    something anyone reads from a sofa.
    """
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: "Xbox Wireless Controller")
    monkeypatch.setattr(cp, "apply_profile", lambda pl, v, p, n, d: cp.ProfileResult(
        ["rpcs3: Player 1 written"],
        ["ryujinx: the SDL2 probe for 045e:02fd could not be run"],
        ["Nintendo Switch"]))

    ws = _RecordingWS()
    asyncio.run(gm._reconcile({}, gm.pads_by_key(_one_pad()), {}, False, ws))

    arrivals = [d for e, d in ws.sent if e == "gp:connected"]
    assert arrivals, "a pad that arrived has to be announced"
    assert arrivals[0]["unconfigured"] == ["Nintendo Switch"], (
        "the toast cannot warn about a system it was never told about")


def test_a_fully_configured_pad_warns_about_nothing(monkeypatch):
    """The warning must not fire on the ordinary case, or it is noise.

    Same reason the green toast existed in the first place: a pad plugged in
    and working is news, and a spurious "not set up for …" on every connection
    would train the player to ignore the one that matters.
    """
    reg._slots.clear(); reg._labels.clear()
    monkeypatch.setattr(cp, "resolve_name", lambda v, p, n: "PS4 Controller")
    monkeypatch.setattr(cp, "apply_profile",
                        lambda pl, v, p, n, d: cp.ProfileResult(["rpcs3: Player 1 written"]))

    ws = _RecordingWS()
    asyncio.run(gm._reconcile({}, gm.pads_by_key(_one_pad()), {}, False, ws))

    arrivals = [d for e, d in ws.sent if e == "gp:connected"]
    assert arrivals and arrivals[0]["unconfigured"] == []


def test_the_real_sdl_starts_in_the_background_and_lists_joysticks():
    """The bundled SDL2.dll loads and its thread runs; a read changes nothing."""
    assert REAL_START() is True, platform_gamepads.unavailable()
    assert platform_gamepads._reader.snapshot() is not None
