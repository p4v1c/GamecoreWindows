"""The mapping wizard's capture — SDL raw events in, an SDL mapping line out.

The dangerous failure here is not a crash. It is a mapping whose every number
is plausible and shifted by one. The backend reads the pad through SDL itself
(`platform/gamepads.py`, raw layer), so button n IS SDL's button n: the layout
is an identity map, and what is tested is that nothing in between renumbers,
drops or invents an input.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import controller_capture as cap             # noqa: E402
from backend.services.configgen import mapping_db                  # noqa: E402
from backend.services.platform import gamepads as platform_gamepads  # noqa: E402

ABS_HAT0X, ABS_HAT0Y, ABS_HAT1X, ABS_HAT1Y = 0x10, 0x11, 0x12, 0x13


# ── SDL's counts to a layout ─────────────────────────────────────────────────

def test_the_layout_is_sdls_own_numbering():
    layout = cap.sdl_layout(11, 6, 1)
    assert layout.counts == (11, 6, 1)
    assert layout.buttons[10] == 10 and layout.axes[5] == 5


def test_each_hat_is_two_pseudo_axes_with_one_index():
    layout = cap.sdl_layout(11, 2, 2)
    assert layout.hats == {ABS_HAT0X: 0, ABS_HAT0Y: 0, ABS_HAT1X: 1, ABS_HAT1Y: 1}


def test_axis_indices_never_reach_the_hat_codes():
    """A device with more axes than fit below the hat codes keeps the first ones:
    an axis index must never be read as a hat."""
    layout = cap.sdl_layout(4, 40, 1)
    assert max(layout.axes) < ABS_HAT0X


# ── one event to one token ───────────────────────────────────────────────────

@pytest.fixture
def layout():
    return cap.sdl_layout(11, 3, 1)


def test_a_press_becomes_a_button_token(layout):
    assert cap.binding_for(layout, cap.EV_KEY, 0, 1) == "b0"
    assert cap.binding_for(layout, cap.EV_KEY, 8, 1) == "b8"


def test_a_release_says_nothing(layout):
    """Binding on the release would record the button the player let go of
    while reaching for the next one."""
    assert cap.binding_for(layout, cap.EV_KEY, 0, 0) is None


def test_an_input_the_pad_does_not_have_is_unmappable(layout):
    assert cap.binding_for(layout, cap.EV_KEY, 30, 1) is None
    assert cap.binding_for(layout, cap.EV_ABS, 4, 30000, CENTRED_STICK) is None


def test_the_hat_bitmask_is_sdls(layout):
    """The pseudo axes fold back into SDL's bitfield: up=1 right=2 down=4
    left=8, and the vertical axis is POSITIVE downwards."""
    assert cap.binding_for(layout, cap.EV_ABS, ABS_HAT0X, 1) == "h0.2"
    assert cap.binding_for(layout, cap.EV_ABS, ABS_HAT0X, -1) == "h0.8"
    assert cap.binding_for(layout, cap.EV_ABS, ABS_HAT0Y, 1) == "h0.4"
    assert cap.binding_for(layout, cap.EV_ABS, ABS_HAT0Y, -1) == "h0.1"
    assert cap.binding_for(layout, cap.EV_ABS, ABS_HAT0X, 0) is None, "centred"


def test_what_sdl_reports_for_a_hat_folds_back_to_the_same_token(layout):
    """End to end through the platform's split: SDL's `up` (1) is `h0.1`."""
    events = platform_gamepads.hat_events(0, 1, "raw")
    tokens = [cap.binding_for(layout, e.type, e.code, e.value) for e in events]
    assert [t for t in tokens if t] == ["h0.1"]


CENTRED_STICK = cap.Axis(minimum=-32768, maximum=32767, rest=0)
# A trigger SDL reports on the same scale, resting at its minimum.
TRIGGER = cap.Axis(minimum=-32768, maximum=32767, rest=-32768)


def test_a_resting_stick_does_not_bind_anything(layout):
    """An analogue stick at rest drifts: the first button the player is asked
    for must not be bound to whichever axis twitched."""
    assert cap.binding_for(layout, cap.EV_ABS, 0, 900, CENTRED_STICK) is None
    assert cap.binding_for(layout, cap.EV_ABS, 0, 30000, CENTRED_STICK) == "a0"
    assert cap.binding_for(layout, cap.EV_ABS, 0, -30000, CENTRED_STICK) == "a0", (
        "pushed the other way is just as pushed")


def test_a_trigger_is_told_from_a_stick_by_where_it_rests(layout):
    """Travel is per direction: a trigger can move the whole scale from its
    minimum, a stick half of it from the centre."""
    assert cap.binding_for(layout, cap.EV_ABS, 2, 32767, TRIGGER) == "a2"
    assert cap.binding_for(layout, cap.EV_ABS, 2, -30000, TRIGGER) is None
    # The same reading two ways: 10000 is 66% of a trigger's travel, 30% of a stick's.
    assert cap.binding_for(layout, cap.EV_ABS, 2, 10000, TRIGGER) == "a2"
    assert cap.binding_for(layout, cap.EV_ABS, 0, 10000, CENTRED_STICK) is None


def test_an_axis_nobody_can_describe_is_unmappable(layout):
    assert cap.binding_for(layout, cap.EV_ABS, 0, 30000, None) is None


def test_a_direction_can_be_narrowed_to_half_an_axis(layout):
    assert cap.half_axis("a2", 30000) == "+a2"
    assert cap.half_axis("a2", -30000) == "-a2"
    assert cap.half_axis("b3", 1) == "b3", "a button has no half"


def test_which_half_is_measured_from_rest_not_from_zero(layout):
    """A half-pulled trigger reads negative on SDL's scale; it is still `+`."""
    assert cap.half_axis("a2", -10000, TRIGGER) == "+a2"
    assert cap.half_axis("a0", -20000, CENTRED_STICK) == "-a0"


# ── the line ─────────────────────────────────────────────────────────────────

GUID = "03000325adde0000efbe000011010000"


def test_the_line_is_a_valid_sdl_mapping():
    line = cap.mapping_line(GUID, "Generic USB Gamepad",
                            {"a": "b0", "b": "b1", "leftx": "a0",
                             "dpup": "h0.1", "lefttrigger": "+a2"})

    assert mapping_db.parse(line), f"our own parser rejects what we write: {line}"
    assert line.startswith(f"{GUID},Generic USB Gamepad,")
    assert line.endswith("platform:Windows,"), "SDL on Windows ignores a Linux line"
    assert "dpup:h0.1" in line and "lefttrigger:+a2" in line


def test_the_field_order_is_stable():
    """The same pad mapped twice must produce the same line, whatever order the
    player happened to press things in — otherwise two captures of one pad
    cannot be diffed, and `upsert` cannot tell an update from a new entry."""
    bindings = {"a": "b0", "b": "b1", "start": "b9", "leftx": "a0"}

    first = cap.mapping_line(GUID, "Pad", bindings)
    second = cap.mapping_line(GUID, "Pad", dict(reversed(list(bindings.items()))))

    assert first == second


def test_a_comma_in_the_name_cannot_break_the_line():
    """The device name is the second CSV field and it comes from the pad, which
    means from a stranger. One comma would shift every binding into the wrong
    column and SDL would read the mapping as a different pad's."""
    line = cap.mapping_line(GUID, "Acme, Inc. Pad", {"a": "b0"})

    guid, name, _bindings = mapping_db.parse(line)
    assert guid == GUID and "," not in name


def test_an_empty_capture_is_refused():
    """A line with a GUID, a name and no binding parses as valid and maps
    nothing. Written to the served database it SHADOWS the community entry for
    that pad — last line wins — so an abandoned wizard would take away a
    mapping that worked."""
    with pytest.raises(ValueError):
        cap.mapping_line(GUID, "Pad", {})
    with pytest.raises(ValueError):
        cap.mapping_line(GUID, "   ", {"a": "b0"})


# ── the session ──────────────────────────────────────────────────────────────

def _one_device(key):
    return lambda v, p: (key, {"sdl9": cap.sdl_layout(11, 0, 0)}, {"sdl9": ()})


@pytest.fixture
def clean_session():
    cap.cancel()
    yield
    cap.cancel()


def test_commit_without_a_session_is_refused(clean_session):
    assert cap.commit({"a": "b0"})["ok"] is False


def test_the_wizard_refuses_to_start_with_two_pads(clean_session, monkeypatch):
    """With two pads connected there is no way to know which one is in the
    owner's hands, and a mapping filed under the wrong GUID is a wrong answer
    that survives reboots."""
    monkeypatch.setattr(cap, "detect_pads",
                        lambda *a, **k: [("054c", "09cc", "A"), ("045e", "02fd", "B")])

    result = cap.start()

    assert result["ok"] is False and "exactly one" in result["error"]


def test_a_pad_no_sdl_can_name_is_refused(clean_session, monkeypatch):
    """A GUID is what SDL looks a mapping up by. Without one there is nothing
    to file the capture under, and writing it under an invented id is the
    "invented identity" this pipeline refuses everywhere else."""
    monkeypatch.setattr(cap, "detect_pads", lambda *a, **k: [("1d79", "0f0f", "Pad")])
    monkeypatch.setattr(cap, "_pad_device", _one_device("aa:bb"))
    monkeypatch.setattr(cap, "sdl_guids", lambda *a, **k: [])

    result = cap.start()

    assert result["ok"] is False and "GUID" in result["error"]


def test_a_capture_is_written_once_per_sdl_identity(clean_session, monkeypatch, tmp_path):
    """The same pad has several GUIDs at once — the host's SDL3 says `0500…`
    where Ryujinx's bundled SDL2 says `0300…`, one byte of bus type apart. A
    mapping filed under one is invisible to the emulators that compute the
    other, which is exactly the "works in RPCS3, dead in Ryujinx" this avoids.
    """
    monkeypatch.setattr(mapping_db, "USER_DB", tmp_path / "user.txt")
    monkeypatch.setattr(mapping_db, "SERVED_DB", tmp_path / "served.txt")
    monkeypatch.setattr(mapping_db, "DB_FILE", tmp_path / "community.txt")
    (tmp_path / "community.txt").write_text("# nothing\n")

    guids = ["05008fe54c050000cc09000000006800", "03008fe54c050000cc09000000006800"]
    monkeypatch.setattr(cap, "detect_pads", lambda *a, **k: [("054c", "09cc", "Pad")])
    monkeypatch.setattr(cap, "_pad_device", _one_device("84:30"))
    monkeypatch.setattr(cap, "sdl_guids", lambda *a, **k: guids)
    monkeypatch.setattr(cap, "display_name", lambda *a: "Generic Pad")

    assert cap.start()["ok"] is True
    result = cap.commit({"a": "b0", "b": "b1", "start": "b9"})

    assert result["ok"] is True
    written = mapping_db.read_user()
    assert len(written) == 2, written
    assert {mapping_db.parse(ln)[0] for ln in written} == set(guids)


def test_committing_closes_the_session(clean_session, monkeypatch, tmp_path):
    """A session left open would let a second commit re-file whatever the
    first wrote."""
    monkeypatch.setattr(mapping_db, "USER_DB", tmp_path / "user.txt")
    monkeypatch.setattr(mapping_db, "SERVED_DB", tmp_path / "served.txt")
    monkeypatch.setattr(mapping_db, "DB_FILE", tmp_path / "community.txt")
    (tmp_path / "community.txt").write_text("# nothing\n")
    monkeypatch.setattr(cap, "detect_pads", lambda *a, **k: [("054c", "09cc", "Pad")])
    monkeypatch.setattr(cap, "_pad_device", _one_device("84:30"))
    monkeypatch.setattr(cap, "sdl_guids", lambda *a, **k: [GUID])
    monkeypatch.setattr(cap, "display_name", lambda *a: "Pad")

    cap.start()
    cap.commit({"a": "b0"})

    assert cap.current() is None
    assert cap.commit({"a": "b1"})["ok"] is False


def test_the_missing_buttons_are_reported(clean_session, monkeypatch, tmp_path):
    """A pad with no right stick is normal; a pad with no Start is a capture
    the player abandoned. The UI can only tell them apart if the backend says
    which required fields are absent."""
    monkeypatch.setattr(mapping_db, "USER_DB", tmp_path / "user.txt")
    monkeypatch.setattr(mapping_db, "SERVED_DB", tmp_path / "served.txt")
    monkeypatch.setattr(mapping_db, "DB_FILE", tmp_path / "community.txt")
    (tmp_path / "community.txt").write_text("# nothing\n")
    monkeypatch.setattr(cap, "detect_pads", lambda *a, **k: [("054c", "09cc", "Pad")])
    monkeypatch.setattr(cap, "_pad_device", _one_device("84:30"))
    monkeypatch.setattr(cap, "sdl_guids", lambda *a, **k: [GUID])
    monkeypatch.setattr(cap, "display_name", lambda *a: "Pad")

    cap.start()
    result = cap.commit({"a": "b0", "b": "b1", "x": "b2", "y": "b3",
                         "dpup": "h0.1", "dpdown": "h0.4",
                         "dpleft": "h0.8", "dpright": "h0.2",
                         "leftshoulder": "b4", "rightshoulder": "b5",
                         "back": "b6", "leftx": "a0", "lefty": "a1"})

    assert result["missing"] == ["start"], result["missing"]
    assert "guide" not in result["missing"], "the guide button is optional"


def test_every_step_is_a_field_sdl_understands():
    """A typo in STEPS would produce a line SDL parses and silently drops the
    field from — the wizard would ask for a button and record it nowhere."""
    known = {
        "a", "b", "x", "y", "back", "guide", "start", "leftstick", "rightstick",
        "leftshoulder", "rightshoulder", "dpup", "dpdown", "dpleft", "dpright",
        "leftx", "lefty", "rightx", "righty", "lefttrigger", "righttrigger",
        "misc1", "paddle1", "paddle2", "paddle3", "paddle4", "touchpad",
    }
    fields = [field for field, _kind, _label in cap.STEPS]

    assert set(fields) <= known, set(fields) - known
    assert len(fields) == len(set(fields)), "a step is listed twice"
    assert cap.OPTIONAL <= set(fields), "an optional field is not a step"


# ── the stream, and how it ends ─────────────────────────────────────────────
#
# A pad unplugged mid-wizard must end the stream: otherwise the wizard sits on
# "press A" for a pad that has gone, and the only other way out of that screen
# was the pad.

import asyncio                                                     # noqa: E402
import time                                                        # noqa: E402

from backend.services.platform.gamepads import GONE, PadEvent       # noqa: E402


def _session(monkeypatch, scripts: dict, age: float = 0.0, rests=None):
    """A session over fake devices; each device's queue is fed its script."""
    def subscribe(device):
        queue = asyncio.Queue()
        for event in scripts.get(device, []):
            queue.put_nowait(event)
        return queue

    monkeypatch.setattr(platform_gamepads, "subscribe", subscribe)
    monkeypatch.setattr(platform_gamepads, "unsubscribe", lambda device, queue: None)
    devices = list(scripts)
    return cap.Session(
        id="probe-1", key="probe", vendor="054c", product="09cc", name="Probe",
        nodes=devices, layouts={d: cap.sdl_layout(11, 3, 1) for d in devices},
        guids=["030000004c050000cc09000011810000"],
        started=time.monotonic() - age,
        rests=rests or {d: (0, 0, -32768) for d in devices})


async def _collect(stream, n):
    out = []
    try:
        for _ in range(n):
            out.append(await asyncio.wait_for(anext(stream), timeout=2))
    finally:
        await stream.aclose()
    return out


def test_a_press_and_its_release_reach_the_wizard(monkeypatch):
    session = _session(monkeypatch, {"sdl0": [PadEvent(cap.EV_KEY, 3, 1, "raw"),
                                              PadEvent(cap.EV_KEY, 3, 0, "raw"), GONE]})
    events = asyncio.run(_collect(cap.events(session), 2))
    assert [(e["binding"], e["pressed"]) for e in events] == [("b3", True), ("b3", False)]


def test_the_pad_layer_is_not_captured(monkeypatch):
    """The game-controller view is SDL's mapping, the thing being made; only
    the raw indices are what a mapping line may contain."""
    session = _session(monkeypatch, {"sdl0": [PadEvent(cap.EV_KEY, 0x13C, 1, "pad"), GONE]})

    async def scenario():
        stream = cap.events(session)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), timeout=2)

    asyncio.run(scenario())


def test_a_trigger_is_captured_from_its_resting_minimum(monkeypatch):
    session = _session(monkeypatch, {"sdl0": [PadEvent(cap.EV_ABS, 2, 32767, "raw"),
                                              PadEvent(cap.EV_ABS, 2, -32768, "raw"), GONE]})
    events = asyncio.run(_collect(cap.events(session), 2))
    assert events[0]["binding"] == "a2" and events[0]["signed"] == "+a2"
    assert events[1]["pressed"] is False


def test_the_stream_ends_when_the_pad_disappears(monkeypatch):
    session = _session(monkeypatch, {"sdl0": [GONE]})

    async def scenario():
        stream = cap.events(session)
        try:
            with pytest.raises(StopAsyncIteration):
                # The timeout is this test's deadlock detector, not a limit the code has.
                await asyncio.wait_for(anext(stream), timeout=2)
        finally:
            await stream.aclose()

    asyncio.run(scenario())


def test_an_expired_session_ends_its_stream_without_a_press(monkeypatch):
    """`current()` forgets a session after SESSION_TTL, and a stream still
    reading a pad for a session that no longer exists answers to nobody."""
    session = _session(monkeypatch, {"sdl0": []}, age=cap.SESSION_TTL + 1)

    async def scenario():
        stream = cap.events(session)
        try:
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(stream), timeout=2)
        finally:
            await stream.aclose()

    asyncio.run(scenario())
