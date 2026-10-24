"""A stand-in for `platform/display.py`: no test may change a real screen.

`fake_screen` (the fixture) replaces the four calls `display.py` makes. Each apply is
recorded as `(width, height, rate, persist)`; `refuse` names modes the driver
turns down; `playing` makes a game resident.
"""
from __future__ import annotations

import pytest

from backend.services import display
from backend.services.platform import display as platform_display

OUTPUT = "\\\\.\\DISPLAY1"
MODES = [(1920, 1080, 60.0), (1920, 1080, 50.0), (1680, 1050, 59.0), (1280, 720, 60.0),
         (1280, 720, 50.0)]


class FakeScreen:
    def __init__(self):
        self.output = OUTPUT
        self.modes = [{"width": w, "height": h, "rate": r} for w, h, r in MODES]
        self.current = {"width": 1920, "height": 1080, "rate": 60.0}
        self.applied: list[tuple[int, int, float, bool]] = []
        self.refuse: set[tuple[int, int]] = set()
        self.playing = False

    def primary_output(self):
        return self.output

    def list_modes(self, output):
        return [dict(m) for m in self.modes]

    def current_mode(self, output):
        return dict(self.current) if self.current else None

    def apply_mode(self, output, width, height, rate, persist=False):
        self.applied.append((width, height, rate, persist))
        if (width, height) in self.refuse:
            return False, "The display does not support that mode"
        if not persist:
            self.current = {"width": width, "height": height, "rate": float(rate)}
        return True, ""

    def changes(self):
        """The applies that changed the screen, without the registry writes."""
        return [a[:3] for a in self.applied if not a[3]]


@pytest.fixture
def fake_screen(monkeypatch):
    fake = FakeScreen()
    monkeypatch.setattr(platform_display, "primary_output", fake.primary_output)
    monkeypatch.setattr(platform_display, "modes", fake.list_modes)
    monkeypatch.setattr(platform_display, "current_mode", fake.current_mode)
    monkeypatch.setattr(platform_display, "apply_mode", fake.apply_mode)
    # `is_running` is a read-only property, replaced on the class: a suspended
    # emulator still holds the swapchain a mode change would invalidate.
    monkeypatch.setattr(type(display.process_manager), "is_running",
                        property(lambda self: fake.playing))
    display._cancel_pending()
    yield fake
    display._cancel_pending()
