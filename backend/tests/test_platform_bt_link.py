"""`platform/bt_link.py`: the address Windows expects, and the real radio lookup.

Nothing is disconnected: conftest replaces `disconnect` for the suite, and the
real one is only asked for an address no device has.
"""
from __future__ import annotations

import ctypes

import pytest

from backend.services.platform import bt_link

REAL_DISCONNECT = bt_link.disconnect


def test_an_address_becomes_the_64_bit_value_windows_expects():
    assert bt_link.address_value("40:1b:5f:b9:ea:8d") == 0x401B5FB9EA8D
    assert bt_link.address_value("40-1B-5F-B9-EA-8D") == 0x401B5FB9EA8D


def test_something_that_is_not_an_address_is_refused_before_any_call():
    with pytest.raises(ValueError):
        REAL_DISCONNECT("sdl0")


def test_the_ioctl_is_the_one_bthioctl_defines():
    # CTL_CODE(FILE_DEVICE_BLUETOOTH 0x41, 0x03, METHOD_BUFFERED, FILE_ANY_ACCESS)
    assert bt_link.IOCTL_BTH_DISCONNECT_DEVICE == (0x41 << 16) | (0x03 << 2)


def test_the_real_radio_lookup_answers_with_or_without_a_radio():
    radios = bt_link._radios()
    for handle in radios:
        ctypes.windll.kernel32.CloseHandle(handle)
    assert isinstance(radios, list)


def test_an_address_no_device_has_is_not_disconnected():
    try:
        assert REAL_DISCONNECT("00:00:00:00:00:01") is False
    except OSError:
        pass        # no radio on this machine (the CI runner)
