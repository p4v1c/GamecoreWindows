"""Resuming a game with several windows (melonDS local multiplayer) raises
them all and leaves their layout alone; one window still gets fullscreen."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend.services import window_focus as wf  # noqa: E402

pytestmark = [pytest.mark.linux_until_port(3), pytest.mark.skipif(not wf._XLIB_OK, reason="python-xlib missing")]


class _Disp:
    def close(self):
        pass

    def intern_atom(self, name):
        return name


def _wire(monkeypatch, windows):
    sent = []
    monkeypatch.setattr(wf, "_open", lambda: _Disp())
    monkeypatch.setattr(wf, "_pids_of", lambda pgid: {10})
    monkeypatch.setattr(wf, "_client_windows", lambda disp: list(windows))
    monkeypatch.setattr(wf, "_window_pid", lambda disp, win: windows[win])
    monkeypatch.setattr(wf, "_send", lambda disp, win, kind, data: sent.append((win, kind, data)))
    return sent


def test_several_game_windows_are_raised_without_fullscreen(monkeypatch):
    sent = _wire(monkeypatch, {"p1w1": 10, "p1w2": 10, "p2w1": 10, "kiosk": 99})
    assert wf._activate_sync("melonds", 42)
    assert [(w, k) for w, k, _ in sent] == [
        ("p1w1", "_NET_ACTIVE_WINDOW"), ("p1w2", "_NET_ACTIVE_WINDOW"),
        ("p2w1", "_NET_ACTIVE_WINDOW")]


def test_a_single_game_window_still_gets_fullscreen_back(monkeypatch):
    sent = _wire(monkeypatch, {"game": 10, "kiosk": 99})
    assert wf._activate_sync("pcsx2", 42)
    kinds = [k for _, k, _ in sent]
    assert kinds == ["_NET_ACTIVE_WINDOW", "_NET_WM_STATE"]
    assert "_NET_WM_STATE_FULLSCREEN" in sent[1][2]
