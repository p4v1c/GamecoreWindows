"""Lutris games run in a nested gamescope window that opens windowed under
the console's KWin, even with gamescope's `-f`: the tile asks the enforcer."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import fullscreen_enforcer as fe              # noqa: E402
from backend.services.catalog import load_catalog                   # noqa: E402
from backend.services.catalog.tiles import tile_entry               # noqa: E402

# WM_CLASS (res_name, res_class) read with xprop on the window of the
# Lutris flatpak's gamescope 3.16.31: SDL2 names it after the binary.
GAMESCOPE_WM_CLASS = ("gamescope-brokey", "gamescope-brokey")


# Without gamescope, Proton (umu) names every game window this, measured on
# FIFA 22 (11/10/2026) — not after the .exe.
PROTON_WM_CLASS = ("steam_app_default", "steam_app_default")


class _Window:
    wm_class = GAMESCOPE_WM_CLASS

    def get_wm_class(self):
        return self.wm_class


class _Display:
    def intern_atom(self, name):
        return 1

    def screen(self):
        prop = type("Prop", (), {"value": [0]})()
        root = type("Root", (), {"get_full_property": lambda self, a, k: prop})()
        return type("Screen", (), {"root": root})()

    def __init__(self, wm_class):
        self.wm_class = wm_class

    def create_resource_object(self, kind, wid):
        win = _Window()
        win.wm_class = self.wm_class
        return win


@pytest.mark.linux_until_port(7)
@pytest.mark.parametrize("wm_class", [GAMESCOPE_WM_CLASS, PROTON_WM_CLASS])
def test_the_lutris_tile_fullscreens_the_game_window(wm_class):
    pack = load_catalog(ROOT / "catalog", ROOT / "config" / "catalog.d")["lutris"]
    fs = tile_entry(pack)["fullscreen"]
    assert fe._find_window(_Display(wm_class), fs["wm_class"]) is not None
