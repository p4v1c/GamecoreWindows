"""The addon hook contract, run for real against a synthetic checkout.

Addons are the part of the split that reaches outside this repository. The CLI
hands an addon's `install.sh` an environment and a directory to write in, and
third-party addons were written against the old one — where `$GAMECORE_PATH`
was writable and the registry lived inside the installation.

Making the root read-only breaks every one of those, and it breaks them in the
worst available way: at install time, on a box in somebody's living room, with
the Addons screen showing a script's stderr. So the contract is versioned, and
an addon that has not been ported is refused **by name, before anything runs**.

Run as a real subprocess rather than by reading the script: the whole point is
what the hook actually receives in its environment and what ends up on disk
afterwards, and neither is visible from a grep of the source.

`GCA_REPO_DIR`, `GAMECORE_PATH` and `GAMECORE_DATA` are all pointed at tmp_path
and `OFFLINE=1` is set, so nothing here can reach the network or the real
/opt/gamecore-addons.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "install" / "bin" / "gamecore-addon"

pytestmark = [pytest.mark.linux_until_port(9), pytest.mark.skipif(
    not shutil.which("git") or not shutil.which("bash"),
    reason="the CLI requires git and bash")]

# What a ported addon's install.sh is entitled to. Written out by the hook so
# the test asserts on what the hook SAW, not on what the CLI meant to send.
_HOOK = """#!/usr/bin/env bash
printf '%s\\n' "$GAMECORE_DATA" "$ADDON_DATA_DIR" "$GAMECORE_ADDON_API" \\
  > "$ADDON_DATA_DIR/hook-env.txt"
"""


def _repo(tmp_path: Path, name: str, meta: dict) -> Path:
    """A checkout holding one addon, with install.sh and uninstall.sh."""
    d = tmp_path / "repo" / "addons" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "addon.json").write_text(json.dumps({"name": name, **meta}))
    for script in ("install.sh", "uninstall.sh"):
        (d / script).write_text(_HOOK)
    return tmp_path / "repo"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    install, data = tmp_path / "install", tmp_path / "data"
    install.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(CLI), *args],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "GCA_REPO_DIR": str(tmp_path / "repo"),
             "GAMECORE_PATH": str(install),
             "GAMECORE_DATA": str(data),
             "OFFLINE": "1"})


def test_an_addon_written_before_the_split_is_refused_by_name(tmp_path):
    """No `api` key means "written when $GAMECORE_PATH was writable".

    The refusal has to name the addon and say what to change, because the
    person reading it is a player on a television, not the addon's author.
    """
    _repo(tmp_path, "legacy", {"version": "1.0", "type": "web"})
    r = _run(tmp_path, "install", "legacy")
    assert r.returncode != 0
    assert "legacy" in r.stderr
    assert "ADDON_DATA_DIR" in r.stderr and '"api"' in r.stderr, (
        "the refusal must say how to port the addon, not just that it failed")


def test_an_addon_speaking_another_api_version_is_refused(tmp_path):
    _repo(tmp_path, "future", {"version": "1.0", "api": 2})
    r = _run(tmp_path, "install", "future")
    assert r.returncode != 0
    assert "api 2" in r.stderr


def test_nothing_is_written_under_the_installation(tmp_path):
    """The property that lets the root be mounted read-only.

    Asserted by walking the tree rather than by trusting the hook: an addon
    that writes under $GAMECORE_PATH does not announce it, and on a read-only
    root the failure surfaces as a broken install months later.
    """
    _repo(tmp_path, "modern", {"version": "1.0", "api": 1})
    r = _run(tmp_path, "install", "modern")
    assert r.returncode == 0, r.stderr
    assert list((tmp_path / "install").rglob("*")) == []


def test_the_registry_lives_on_the_data_side(tmp_path):
    """`config/addons.json` is the CLI's half of a contract the backend reads
    (routers/addons.py). Both had to move together, or the Addons screen shows
    an empty list on a box that has addons installed."""
    _repo(tmp_path, "modern", {"version": "1.0", "api": 1, "type": "web", "port": 9000})
    assert _run(tmp_path, "install", "modern").returncode == 0
    registry = tmp_path / "data" / "config" / "addons.json"
    assert registry.exists(), "the registry must be written under GAMECORE_DATA"
    assert "modern" in json.loads(registry.read_text())


def test_the_hook_is_handed_a_writable_directory_of_its_own(tmp_path):
    """An addon should not have to know how GameCore lays out its data to keep
    a config file, so the directory exists before install.sh runs."""
    _repo(tmp_path, "modern", {"version": "1.0", "api": 1})
    assert _run(tmp_path, "install", "modern").returncode == 0
    seen = (tmp_path / "data" / "addons" / "modern" / "hook-env.txt").read_text().split()
    assert seen == [str(tmp_path / "data"),
                    str(tmp_path / "data" / "addons" / "modern"), "1"]


def test_an_unported_addon_can_still_be_removed(tmp_path):
    """The gate is on install, never on remove.

    A box updated to this release has addons installed by the OLD CLI, none of
    which declares an api version. Refusing to remove them would strand the
    player with something they cannot uninstall from the screen that installed
    it — the update would have taken away the exit.
    """
    _repo(tmp_path, "legacy", {"version": "1.0"})
    (tmp_path / "data" / "config").mkdir(parents=True)
    (tmp_path / "data" / "config" / "addons.json").write_text(
        json.dumps({"legacy": {"version": "1.0"}}))
    r = _run(tmp_path, "remove", "legacy")
    assert r.returncode == 0, r.stderr
    assert json.loads((tmp_path / "data" / "config" / "addons.json").read_text()) == {}


# ── Where the data is, when nobody said ─────────────────────────────────────
#
# The CLI is typed at a shell, and the shell has no GAMECORE_DATA in it. The
# variable lives on the backend's systemd unit — the one place the migration
# procedure sets it — so that is where the CLI looks. Otherwise the first
# `gamecore-addon update` after a data move would hand every addon's install.sh
# the OLD root, and ROM uploads would land in a tree the box no longer reads.

def _fake_systemctl(tmp_path: Path, environment: str) -> Path:
    """A `systemctl` on PATH that answers `show -p Environment --value`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "systemctl"
    fake.write_text("#!/usr/bin/env bash\n"
                    "case \"$*\" in\n"
                    f"  *'-p Environment'*) printf '%s\\n' '{environment}' ;;\n"
                    "  *) exit 0 ;;\n"
                    "esac\n")
    fake.chmod(0o755)
    return bin_dir


def _run_unset(tmp_path: Path, bin_dir: Path | None, *args: str,
               data_env: str | None = None) -> subprocess.CompletedProcess:
    """Like `_run`, but GAMECORE_DATA is NOT in the environment unless given."""
    install = tmp_path / "install"
    install.mkdir(exist_ok=True)
    env = {"PATH": (f"{bin_dir}:" if bin_dir else "") + "/usr/bin:/bin",
           "HOME": str(tmp_path), "GCA_REPO_DIR": str(tmp_path / "repo"),
           "GAMECORE_PATH": str(install), "OFFLINE": "1"}
    if data_env is not None:
        env["GAMECORE_DATA"] = data_env
    return subprocess.run(["bash", str(CLI), *args],
                          capture_output=True, text=True, timeout=120, env=env)


def _registry_with(root: Path, name: str) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "addons.json").write_text(json.dumps(
        {name: {"version": "9.9", "type": "web", "label": name}}))


def test_the_data_root_is_read_from_the_backend_unit_when_the_shell_has_none(tmp_path):
    _repo(tmp_path, "probe", {"version": "1.0", "type": "web", "api": 1})
    moved = tmp_path / "userdata"
    _registry_with(moved, "from-the-unit")
    _registry_with(tmp_path / "install", "from-the-install")

    bin_dir = _fake_systemctl(tmp_path, f"GAMECORE_PATH=/x GAMECORE_DATA={moved} FOO=bar")
    r = _run_unset(tmp_path, bin_dir, "list", "--json")
    assert r.returncode == 0, r.stderr
    installed = json.loads(r.stdout)["installed"]
    assert "from-the-unit" in installed and "from-the-install" not in installed, installed


def test_an_explicit_data_root_still_wins_over_the_unit(tmp_path):
    """The installer, a sandbox and the tests all set it on purpose."""
    _repo(tmp_path, "probe", {"version": "1.0", "type": "web", "api": 1})
    explicit = tmp_path / "explicit"
    _registry_with(explicit, "from-the-env")
    _registry_with(tmp_path / "userdata", "from-the-unit")

    bin_dir = _fake_systemctl(tmp_path, f"GAMECORE_DATA={tmp_path / 'userdata'}")
    r = _run_unset(tmp_path, bin_dir, "list", "--json", data_env=str(explicit))
    assert r.returncode == 0, r.stderr
    installed = json.loads(r.stdout)["installed"]
    assert "from-the-env" in installed and "from-the-unit" not in installed, installed


def test_without_a_unit_the_data_root_falls_back_to_the_install(tmp_path):
    """A box from before the split, an ISO, a sandbox with no systemd: the
    old default, byte for byte, and no error for a systemctl that is absent
    or that knows no such unit."""
    _repo(tmp_path, "probe", {"version": "1.0", "type": "web", "api": 1})
    _registry_with(tmp_path / "install", "from-the-install")

    # A systemctl that fails outright (no systemd, no bus). Faked rather than
    # removed from PATH: /usr/bin is on it and holds the REAL one, and on a
    # box whose backend unit already carries GAMECORE_DATA the real one would
    # answer — which is exactly how this test once passed for the wrong reason
    # and then failed the evening the reference box migrated.
    bin_dir = tmp_path / "bin-broken"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text("#!/usr/bin/env bash\nexit 1\n")
    (bin_dir / "systemctl").chmod(0o755)
    r = _run_unset(tmp_path, bin_dir, "list", "--json")
    assert r.returncode == 0, r.stderr
    assert "from-the-install" in json.loads(r.stdout)["installed"]

    # A systemctl that answers with no GAMECORE_DATA (unit predates the split).
    bin_dir = _fake_systemctl(tmp_path, "GAMECORE_PATH=/opt/GameCore GAMECORE_BACKEND_PORT=8765")
    r = _run_unset(tmp_path, bin_dir, "list", "--json")
    assert r.returncode == 0, r.stderr
    assert "from-the-install" in json.loads(r.stdout)["installed"]

    # A systemctl that answers nothing (no such unit): same.
    bin_dir = _fake_systemctl(tmp_path, "")
    r = _run_unset(tmp_path, bin_dir, "list", "--json")
    assert r.returncode == 0, r.stderr
    assert "from-the-install" in json.loads(r.stdout)["installed"]
