"""A stand-in for `platform/volumes.py`: no test lists or ejects a real disk.

`disks` (the fixture) answers `list_volumes()` from `rows` and `eject()` from
`eject_result`, recording each eject. `row(**over)` builds one drive; its
`mountpoint` is usually a `tmp_path` folder standing for `E:\\`.
"""
from __future__ import annotations

import pytest

from backend.services.platform import volumes as platform_volumes


def row(**over) -> dict:
    base = dict(name="E:", path="E:", label="ROMS", uuid="A4E2-19F7", fstype="ntfs",
                mountpoint="E:\\", removable=True, size="931.5G")
    return {**base, **over}


class FakeDisks:
    def __init__(self):
        self.rows: list[dict] = []
        self.ejected: list[str] = []
        self.eject_result = (True, "Safe to unplug")

    def list_volumes(self):
        return [dict(r) for r in self.rows]

    def eject(self, letter):
        self.ejected.append(letter)
        return self.eject_result


@pytest.fixture
def disks(monkeypatch):
    fake = FakeDisks()
    monkeypatch.setattr(platform_volumes, "list_volumes", fake.list_volumes)
    monkeypatch.setattr(platform_volumes, "eject", fake.eject)
    return fake
