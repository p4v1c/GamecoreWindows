"""configgen.detect_pads: which devices get a player slot in emulator configs.

SDL's snapshot (`platform/gamepads.py` `pads()`) is replaced with a list.
"""
from __future__ import annotations

import pytest

from backend.services.configgen import controllers
from backend.services.platform import gamepads as platform_gamepads


def pad(device, name, vendor, product, serial="", is_pad=True):
    return platform_gamepads.PadInfo(device=device, name=name, vendor=vendor,
                                     product=product, guid="0" * 32, serial=serial,
                                     bus=5, is_pad=is_pad, has_guide=True)


@pytest.fixture
def connected(monkeypatch):
    pads: list = []
    monkeypatch.setattr(platform_gamepads, "pads", lambda: list(pads))
    return pads


def test_pads_come_in_the_order_they_connected(connected):
    connected += [pad("sdl3", "PS4 Controller", "054c", "09cc"),
                  pad("sdl7", "Xbox Series X Controller", "045e", "0b13")]
    assert controllers.detect_pads() == [("054c", "09cc", "PS4 Controller"),
                                         ("045e", "0b13", "Xbox Series X Controller")]


def test_a_wheel_does_not_push_pad_two_to_player_three(connected):
    connected += [pad("sdl1", "PS4 Controller", "054c", "09cc"),
                  pad("sdl2", "Racing Wheel", "046d", "c24f", is_pad=False),
                  pad("sdl3", "Xbox Series X Controller", "045e", "0b13")]
    assert [p[2] for p in controllers.detect_pads()] == ["PS4 Controller",
                                                        "Xbox Series X Controller"]


def test_one_pad_listed_twice_under_one_address_is_one_player(connected):
    connected += [pad("sdl1", "PS4 Controller", "054c", "09cc", serial="84:30:95:07:c8:1c"),
                  pad("sdl2", "PS4 Controller", "054c", "09cc", serial="84:30:95:07:c8:1c")]
    assert len(controllers.detect_pads()) == 1


def test_the_d_pad_shape_is_what_sdl_counts(connected):
    with_hat = pad("sdl1", "Generic", "0079", "0006")
    with_hat.counts = (12, 4, 1)
    connected.append(with_hat)
    assert controllers.pad_has_hat("0079", "0006") is True
    assert controllers.pad_has_hat("dead", "beef") is None
