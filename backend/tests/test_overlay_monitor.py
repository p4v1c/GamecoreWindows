"""The overlay monitor's contract with Electron.

It runs as a subprocess of `electron/main.js` and speaks JSON-lines over stdio:
one object per line, flushed. Nothing else enforces that — a stray `print()`, a
missing flush, or an object spread over two lines and the bezel silently stops
following the emulator's window, on a box, with no error anywhere.

X11 is not exercised here. What is exercised is the protocol, and the two
degraded paths a box actually hits: a Wayland session, and python-xlib absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import overlay_monitor as om


# ── the wire format ────────────────────────────────────────────────────────

def test_every_event_is_one_line_of_json(capsys):
    """`electron/main.js` splits stdout on newlines and parses each piece."""
    om.emit({"event": "window:opened", "system_id": "pcsx2"})
    om.emit({"event": "window:closed", "system_id": "pcsx2"})
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(x)["event"] for x in lines] == ["window:opened", "window:closed"]


def test_an_event_carrying_a_newline_still_occupies_one_line(capsys):
    """Window titles come from the emulator and are not ours to trust —
    json.dumps escapes the newline rather than emitting it."""
    om.emit({"event": "error", "message": "two\nlines"})
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["message"] == "two\nlines"


def test_emit_error_is_a_normal_event_not_a_stderr_write(capsys):
    """It travels the same channel as everything else, or Electron never sees
    it: only stdout is parsed."""
    om.emit_error("no display")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"event": "error", "message": "no display"}


# ── the degraded paths ─────────────────────────────────────────────────────

def test_a_wayland_session_leaves_the_monitor_without_a_manager(monkeypatch):
    """Overlays are X11-only. On a Wayland session the monitor must construct
    and do nothing, not raise — the kiosk still has to start."""
    monkeypatch.setattr(om, "_WAYLAND_SESSION", True)
    assert om.OverlayMonitor()._mgr is None


@pytest.mark.linux_until_port(4)
def test_without_python_xlib_it_also_constructs(monkeypatch):
    monkeypatch.setattr(om, "_WAYLAND_SESSION", False)
    monkeypatch.setattr(om, "_XLIB_OK", False)
    assert om.OverlayMonitor()._mgr is None


def test_stopping_a_monitor_that_never_started_is_not_an_error(monkeypatch):
    """`main.js` sends `quit` on shutdown regardless of whether a game ran."""
    monkeypatch.setattr(om, "_WAYLAND_SESSION", True)
    mon = om.OverlayMonitor()
    mon.stop()
    mon.stop()
    assert mon._thread is None


# ── the screen measurement ─────────────────────────────────────────────────
#
# The X11 capture itself needs a display and a running emulator and is not
# exercised anywhere. What is exercised is the part around it: when the monitor
# looks at all, what it puts on the wire, and that every way a capture can fail
# ends in silence rather than in an exception on a launch path.

# A 20x20 window whose bezel announces a pillarbox, against an emulator that
# letterboxes instead — the shape of the mismatch this exists to find.
RECT = {"x": 0, "y": 0, "w": 20, "h": 20}
ANNOUNCED = {"x": 5, "y": 0, "w": 10, "h": 20}
DRAWN = (0, 5, 20, 10)


class FakeManager:
    """An X11Manager that returns frames from a script instead of a screen."""

    def __init__(self, *frames):
        self._frames = list(frames)
        self.captures = 0

    def window_exists(self, wid):
        return True

    def capture(self, wid, w, h):
        self.captures += 1
        shot = self._frames.pop(0) if self._frames else None
        return None if shot is None else (shot, w * 4)


def lit(w, h, box):
    """A black frame with one lit rectangle, laid out the way X returns one."""
    out = bytearray()
    for y in range(h):
        row = bytearray(w * 4)
        if box and box[1] <= y < box[1] + box[3]:
            for x in range(box[0], box[0] + box[2]):
                row[x * 4] = row[x * 4 + 1] = row[x * 4 + 2] = 200
        out += row
    return bytes(out)


def _monitor(monkeypatch, mgr):
    monkeypatch.setattr(om, "_WAYLAND_SESSION", True)   # no real display wanted
    mon = om.OverlayMonitor()
    mon._mgr = mgr
    # The samples are 1 s and 2.5 s apart in production; a test must not be.
    monkeypatch.setattr(mon._stop, "wait", lambda _s: False)
    return mon


def test_two_agreeing_samples_are_reported(monkeypatch, capsys):
    """A pillarbox announced, an emulator letterboxing instead."""
    frame = lit(20, 20, DRAWN)
    mon = _monitor(monkeypatch, FakeManager(frame, frame))
    mon._measure("duckstation", 1, RECT, ANNOUNCED)

    msg = json.loads(capsys.readouterr().out.strip())
    assert msg["event"] == "window:measured"
    assert msg["measured"] == {"x": 0, "y": 5, "w": 20, "h": 10}
    # The ANNOUNCED rectangle, not the measured one: it is the key the backend
    # files the correction under, and sending the corrected value instead would
    # make every launch relearn against its own last answer.
    assert msg["announced"] == ANNOUNCED
    assert msg["window"] == {"w": 20, "h": 20}


def test_samples_that_disagree_report_nothing(monkeypatch, capsys):
    """A loading screen followed by the game. Believing either would be a
    coin toss, and the losing side moves a hole that was right."""
    mon = _monitor(monkeypatch, FakeManager(lit(20, 20, (9, 9, 2, 2)),
                                            lit(20, 20, DRAWN)))
    mon._measure("duckstation", 1, RECT, ANNOUNCED)
    events = [json.loads(x)["event"] for x in capsys.readouterr().out.splitlines()]
    assert "window:measured" not in events


def test_an_implausible_agreement_reports_nothing(monkeypatch, capsys):
    """Two samples of the same logo agree perfectly and mean nothing."""
    logo = lit(20, 20, (9, 9, 2, 2))
    mon = _monitor(monkeypatch, FakeManager(logo, logo))
    mon._measure("duckstation", 1, RECT, ANNOUNCED)
    events = [json.loads(x)["event"] for x in capsys.readouterr().out.splitlines()]
    assert "window:measured" not in events


def test_a_frame_filling_the_whole_window_reports_nothing(monkeypatch, capsys):
    """Deliberately refused, and the refusal costs a real case.

    An emulator stretching a 4:3 game across the window really does draw edge
    to edge, and correcting to that would be right. But a bright splash screen
    measures identically, and believing one would cache "no bars here" and
    retire a perfectly good bezel for good. A missed correction leaves the box
    exactly as it is today; a false one throws artwork away, so the tie goes to
    doing nothing.
    """
    full = lit(20, 20, (0, 0, 20, 20))
    mon = _monitor(monkeypatch, FakeManager(full, full))
    mon._measure("duckstation", 1, RECT, ANNOUNCED)
    events = [json.loads(x)["event"] for x in capsys.readouterr().out.splitlines()]
    assert "window:measured" not in events


def test_a_failed_capture_is_silent(monkeypatch, capsys):
    """GetImage refusing is normal on a compositor that redirects the window.
    It must cost the measurement and nothing else."""
    mon = _monitor(monkeypatch, FakeManager(None, None))
    mon._measure("duckstation", 1, RECT, ANNOUNCED)
    assert "window:measured" not in capsys.readouterr().out


def test_a_game_that_ends_during_the_wait_stops_the_capture(monkeypatch, capsys):
    """`_stop.wait` returning True is the game already over. Reading 8 MB off
    a window that is being torn down is how the monitor outlives the emulator."""
    mgr = FakeManager(lit(20, 20, DRAWN), lit(20, 20, DRAWN))
    monkeypatch.setattr(om, "_WAYLAND_SESSION", True)
    mon = om.OverlayMonitor()
    mon._mgr = mgr
    monkeypatch.setattr(mon._stop, "wait", lambda _s: True)
    mon._measure("duckstation", 1, RECT, ANNOUNCED)
    assert mgr.captures == 0
    assert capsys.readouterr().out == ""


# ── several game windows ───────────────────────────────────────────────────

class WatchedManager:
    """Enough of an X11Manager for `_run`: one window, then `extra` siblings."""

    def __init__(self, siblings_after: int):
        self.calls = 0
        self.siblings_after = siblings_after

    def find_window(self, classes):
        return 7

    def get_rect(self, wid):
        return dict(RECT)

    def force_rect(self, wid, x, y, w, h):
        pass

    def window_exists(self, wid):
        return True

    def count_main_windows(self, classes):
        self.calls += 1
        return 2 if self.calls >= self.siblings_after else 1


def test_the_bezel_steps_aside_when_the_emulator_opens_more_game_windows(monkeypatch, capsys):
    """melonDS local multiplayer: one window per player, laid out by its
    launcher. Holding the first in the bezel rect would undo that layout."""
    monkeypatch.setattr(om.time, "sleep", lambda _s: None)
    mon = _monitor(monkeypatch, WatchedManager(siblings_after=2))
    mon._run("melonds", {"window_rect": RECT, "wm_class": {"linux": ["melonDS"]}})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1] == {"event": "window:closed", "system_id": "melonds",
                          "reason": "several-windows"}
    assert any(e["event"] == "window:ready" for e in events)


class SiblingsFromStart(WatchedManager):
    def __init__(self):
        super().__init__(siblings_after=1)
        self.forced = 0

    def force_rect(self, wid, x, y, w, h):
        self.forced += 1


def test_a_resumed_multi_window_game_is_never_forced_into_the_bezel(monkeypatch, capsys):
    """On a resume every melonDS window exists already: no force_rect at all."""
    monkeypatch.setattr(om.time, "sleep", lambda _s: None)
    mgr = SiblingsFromStart()
    mon = _monitor(monkeypatch, mgr)
    mon._run("melonds", {"window_rect": RECT, "wm_class": {"linux": ["melonDS"]}})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert mgr.forced == 0
    assert events[-1]["reason"] == "several-windows"
    assert not any(e["event"] == "window:ready" for e in events)
