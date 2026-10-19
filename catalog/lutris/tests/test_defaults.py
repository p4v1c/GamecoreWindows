"""Lutris defaults and the setup run: written where absent, never over the owner."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from backend.tests.linux_until_port import linux_until_port_module

linux_until_port_module(7)  # lutris_setup imports fcntl (flock)

yaml = pytest.importorskip("yaml")

FILES = Path(__file__).resolve().parents[1] / "files"
sys.path.insert(0, str(FILES))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"test_{name}", FILES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


defaults = _load("lutris_defaults")
paths = _load("lutris_paths")
setup = _load("lutris_setup")

APP = "net.lutris.Lutris"
TAG = "GE-Proton11-7"


def read(path):
    return yaml.safe_load(path.read_text())


def test_a_fresh_lutris_gets_the_fast_paths_and_ge_proton(tmp_path):
    games = tmp_path / "games"
    lines = defaults.apply(tmp_path, proton=TAG, ours={TAG}, games_dir=games)
    assert len(lines) == 2
    assert read(tmp_path / "runners/wine.yml") == {"wine": {
        "dxvk": True, "vkd3d": True, "esync": True, "fsync": True, "version": TAG}}
    assert read(tmp_path / "system.yml") == {"system": {"gamemode": True,
                                                        "game_path": str(games)}}
    assert games.is_dir()


def test_the_owners_values_are_kept(tmp_path):
    (tmp_path / "runners").mkdir()
    (tmp_path / "runners/wine.yml").write_text(
        "wine:\n  version: lutris-7.2\n  dxvk: false\nsystem:\n  mangohud: true\n")
    (tmp_path / "system.yml").write_text("system:\n  gamemode: false\n  game_path: /games\n")
    defaults.apply(tmp_path, proton=TAG, ours=set(), games_dir=tmp_path / "g")
    wine = read(tmp_path / "runners/wine.yml")
    assert wine["wine"]["version"] == "lutris-7.2" and wine["wine"]["dxvk"] is False
    assert wine["wine"]["vkd3d"] is True and wine["system"] == {"mangohud": True}
    assert read(tmp_path / "system.yml") == {"system": {"gamemode": False, "game_path": "/games"}}
    assert not (tmp_path / "g").exists()


def test_the_version_follows_our_newer_build_only(tmp_path):
    assert defaults.pick_version(None, set(), TAG) == TAG
    assert defaults.pick_version("GE-Proton10-25", {"GE-Proton10-25"}, TAG) == TAG
    assert defaults.pick_version("GE-Proton10-25", set(), TAG) is None
    assert defaults.pick_version("ge-proton", {"GE-Proton10-25"}, TAG) is None
    assert defaults.pick_version(TAG, {TAG}, TAG) is None


def test_an_unreadable_config_is_left_alone(tmp_path):
    (tmp_path / "runners").mkdir()
    broken = "wine: [unclosed\n"
    (tmp_path / "runners/wine.yml").write_text(broken)
    lines = defaults.apply(tmp_path, proton=TAG, ours=set(), games_dir=None)
    assert (tmp_path / "runners/wine.yml").read_text() == broken
    assert any("left alone" in line for line in lines)


def test_lutris_config_lives_in_data_unless_config_lutris_exists(tmp_path):
    root = tmp_path / ".var/app" / APP
    assert paths.config_dir(tmp_path, APP) == root / "data/lutris"
    (root / "config/lutris").mkdir(parents=True)
    assert paths.config_dir(tmp_path, APP) == root / "config/lutris"


def test_a_custom_runner_dir_is_followed(tmp_path):
    data = tmp_path / ".var/app" / APP / "data/lutris"
    data.mkdir(parents=True)
    (data / "lutris.conf").write_text("[lutris]\nrunner_dir = /big/runners\n")
    assert paths.wine_dir(tmp_path, APP) == Path("/big/runners/wine")


def test_a_setup_run_installs_configures_and_records(tmp_path, monkeypatch):
    def fetch(wine_dir, tag, log):
        (wine_dir / tag).mkdir(parents=True)
        (wine_dir / tag / "proton").write_text("")
        return True
    monkeypatch.setattr(setup.lutris_proton, "latest_tag", lambda log: TAG)
    monkeypatch.setattr(setup.lutris_proton, "fetch", fetch)
    assert setup.run(tmp_path, APP, None) == 0
    config = tmp_path / ".var/app" / APP / "data/lutris"
    assert read(config / "runners/wine.yml")["wine"]["version"] == TAG
    assert json.loads((setup.state_dir(tmp_path) / "state.json").read_text()) == {"proton": [TAG]}
    # A second run with nothing new changes nothing and still succeeds.
    monkeypatch.setattr(setup.lutris_proton, "fetch", lambda *a: pytest.fail("refetched"))
    assert setup.run(tmp_path, APP, None) == 0


def test_offline_the_newest_build_we_have_is_still_the_default(tmp_path, monkeypatch):
    wine = paths.wine_dir(tmp_path, APP)
    (wine / "GE-Proton10-25").mkdir(parents=True)
    (wine / "GE-Proton10-25" / "proton").write_text("")
    setup.save_state(tmp_path, {"proton": ["GE-Proton10-25"]})
    monkeypatch.setattr(setup.lutris_proton, "latest_tag", lambda log: None)
    assert setup.run(tmp_path, APP, None) == 1
    config = tmp_path / ".var/app" / APP / "data/lutris"
    assert read(config / "runners/wine.yml")["wine"]["version"] == "GE-Proton10-25"


def test_nothing_happens_when_lutris_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "lutris_installed", lambda app: False)
    monkeypatch.setattr(setup, "run", lambda *a: pytest.fail("ran without Lutris"))
    assert setup.main(["--app-id", APP, "--home", str(tmp_path)]) == 0
