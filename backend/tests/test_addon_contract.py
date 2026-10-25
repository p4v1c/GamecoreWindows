"""The addon hook contract, run for real against a synthetic checkout.

The CLI hands an addon's `install.ps1` an environment and a directory to write
in. Third-party addons are written against it, so an addon that has not been
ported is refused **by name, before anything runs**, and what a hook receives
is asserted from inside a real hook run by Windows PowerShell, not from a read
of the CLI's source.

`GCA_REPO_DIR`, `GAMECORE_PATH`, `GAMECORE_DATA` and `GAMECORE_SESSION_ENV`
all point into tmp_path, `GCA_ADDON_PYTHON` names the running interpreter, and
the repo arrives offline or from a `file://` archive: nothing here reaches the
network, `%ProgramData%` or a real data root.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "install" / "bin" / "gamecore-addon"


def _load():
    loader = importlib.machinery.SourceFileLoader("gamecore_addon", str(CLI))
    spec = importlib.util.spec_from_loader("gamecore_addon", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gca = _load()

# What a ported addon's install.ps1 is entitled to, written out by the hook so
# the test asserts on what the hook SAW, not on what the CLI meant to send.
_HOOK = """$lines = @($env:GAMECORE_DATA, $env:ADDON_DATA_DIR, $env:GAMECORE_ADDON_API,
    $env:GAMECORE_ADDON_PYTHON, $env:ADDON_DIR, [string](Test-Path -LiteralPath $env:ADDON_DATA_DIR))
[IO.File]::WriteAllLines((Join-Path $env:ADDON_DATA_DIR '{script}.txt'), [string[]]$lines)
Add-Content -LiteralPath (Join-Path $env:GAMECORE_DATA 'hooks.log') -Value "$(Split-Path -Leaf $env:ADDON_DIR) {script}"
"""


def _addon(repo: Path, name: str, meta: dict, *, install_exit: int = 0) -> Path:
    d = repo / "addons" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "addon.json").write_text(json.dumps({"name": name, **meta}), encoding="utf-8")
    for script in ("install", "uninstall"):
        body = _HOOK.replace("{script}", script)
        if script == "install" and install_exit:
            body += f"exit {install_exit}\n"
        (d / f"{script}.ps1").write_text(body, encoding="ascii")
    return d


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    install, data = tmp_path / "install", tmp_path / "data"
    install.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GAMECORE_", "GCA_", "ADDON_"))}
    env.update({"GCA_REPO_DIR": str(tmp_path / "repo"), "GAMECORE_PATH": str(install),
                "GAMECORE_DATA": str(data), "GCA_ADDON_PYTHON": sys.executable,
                "GAMECORE_SESSION_ENV": str(tmp_path / "no-session.env"), "OFFLINE": "1",
                "PYTHONUTF8": "1"})
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


def _run(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                          timeout=180, env=env or _env(tmp_path))


def _hooks_run(tmp_path: Path) -> list[str]:
    log = tmp_path / "data" / "hooks.log"
    return log.read_text(encoding="utf-8").split("\n")[:-1] if log.exists() else []


# ── the gate ─────────────────────────────────────────────────────────────────

def test_an_addon_written_before_the_split_is_refused_by_name(tmp_path):
    """No `api` key: the refusal names the addon and says how to port it."""
    _addon(tmp_path / "repo", "legacy", {"version": "1.0", "type": "web"})
    r = _run(tmp_path, "install", "legacy")
    assert r.returncode != 0
    assert "legacy" in r.stderr
    assert "ADDON_DATA_DIR" in r.stderr and '"api"' in r.stderr
    assert _hooks_run(tmp_path) == [], "nothing may run before the refusal"


def test_an_addon_speaking_another_api_version_is_refused(tmp_path):
    _addon(tmp_path / "repo", "future", {"version": "1.0", "api": 2})
    r = _run(tmp_path, "install", "future")
    assert r.returncode != 0
    assert "api 2" in r.stderr


def test_an_unported_addon_can_still_be_removed(tmp_path):
    """The gate is on install, never on remove: refusing would strand the player."""
    _addon(tmp_path / "repo", "legacy", {"version": "1.0"})
    registry = tmp_path / "data" / "config" / "addons.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"legacy": {"version": "1.0"}}), encoding="utf-8")
    r = _run(tmp_path, "remove", "legacy")
    assert r.returncode == 0, r.stderr
    assert json.loads(registry.read_text(encoding="utf-8")) == {}
    assert _hooks_run(tmp_path) == ["legacy uninstall"]


@pytest.mark.parametrize("name", ["..", "Rom-Manager", "-x", "a/b", "a\\b", ""])
def test_a_name_that_could_leave_the_repo_is_refused(tmp_path, name):
    r = _run(tmp_path, "remove", "--", name)
    assert r.returncode != 0
    assert "invalid addon name" in r.stderr


# ── what a hook receives ─────────────────────────────────────────────────────

def test_the_hook_gets_its_own_directory_the_api_and_the_addon_python(tmp_path):
    d = _addon(tmp_path / "repo", "modern", {"version": "1.0", "api": 1})
    r = _run(tmp_path, "install", "modern")
    assert r.returncode == 0, r.stderr
    own = tmp_path / "data" / "addons" / "modern"
    seen = (own / "install.txt").read_text(encoding="utf-8").splitlines()
    assert seen == [str(tmp_path / "data"), str(own), "1", sys.executable, str(d), "True"]


def test_nothing_is_written_under_the_installation(tmp_path):
    """The property that lets an update replace the code root wholesale."""
    _addon(tmp_path / "repo", "modern", {"version": "1.0", "api": 1})
    assert _run(tmp_path, "install", "modern").returncode == 0
    assert list((tmp_path / "install").rglob("*")) == []


def test_the_registry_lives_on_the_data_side(tmp_path):
    _addon(tmp_path / "repo", "modern", {"version": "1.0", "api": 1, "type": "web",
                                         "port": 8790, "path": "/modern", "label": "Modern"})
    assert _run(tmp_path, "install", "modern").returncode == 0
    entry = json.loads((tmp_path / "data" / "config" / "addons.json").read_text(encoding="utf-8"))["modern"]
    assert {k: entry[k] for k in ("version", "type", "port", "path", "label")} == {
        "version": "1.0", "type": "web", "port": 8790, "path": "/modern", "label": "Modern"}


def test_a_manifest_saved_with_a_bom_is_read(tmp_path):
    """Notepad and zips made on Windows put a BOM in front of addon.json."""
    d = _addon(tmp_path / "repo", "bom", {"version": "1.0", "api": 1})
    (d / "addon.json").write_text(json.dumps({"name": "bom", "version": "1.0", "api": 1}),
                                  encoding="utf-8-sig")
    r = _run(tmp_path, "install", "bom")
    assert r.returncode == 0, r.stderr


def test_a_failing_hook_fails_the_install_and_leaves_no_registry_entry(tmp_path):
    _addon(tmp_path / "repo", "broken", {"version": "1.0", "api": 1}, install_exit=3)
    r = _run(tmp_path, "install", "broken")
    assert r.returncode != 0
    assert "exit 3" in r.stderr
    assert not (tmp_path / "data" / "config" / "addons.json").exists()


# ── where the data is, when nobody said ─────────────────────────────────────

def _session_env(tmp_path: Path, data: Path) -> Path:
    path = tmp_path / "session.env"
    path.write_text(f"# test\nGAMECORE_PATH={tmp_path / 'install'}\nGAMECORE_DATA={data}\n", encoding="utf-8")
    return path


def _registry_with(root: Path, name: str) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "addons.json").write_text(json.dumps({name: {"version": "9.9"}}), encoding="utf-8")


def test_a_prompt_without_gamecore_data_reads_the_installers_session_env(tmp_path):
    """A prompt has no GAMECORE_DATA; session.env is where the installer wrote the real one."""
    moved = tmp_path / "D-drive"
    _registry_with(moved, "from-session-env")
    _registry_with(tmp_path / "install", "from-the-install")
    env = _env(tmp_path, GAMECORE_DATA=None, GCA_REPO_DIR=None,
               GAMECORE_SESSION_ENV=str(_session_env(tmp_path, moved)))
    r = _run(tmp_path, "list", "--json", env=env)
    assert r.returncode == 0, r.stderr
    assert list(json.loads(r.stdout)["installed"]) == ["from-session-env"]


def test_an_explicit_data_root_wins_over_session_env(tmp_path):
    """The backend, the installer and a sandbox all set it on purpose."""
    explicit = tmp_path / "explicit"
    _registry_with(explicit, "from-the-env")
    env = _env(tmp_path, GAMECORE_DATA=str(explicit),
               GAMECORE_SESSION_ENV=str(_session_env(tmp_path, tmp_path / "other")))
    r = _run(tmp_path, "list", "--json", env=env)
    assert list(json.loads(r.stdout)["installed"]) == ["from-the-env"]


def test_without_session_env_the_data_root_is_the_code_root(tmp_path):
    """A checkout: the same default as backend/services/paths.py."""
    _registry_with(tmp_path / "install", "from-the-install")
    r = _run(tmp_path, "list", "--json", env=_env(tmp_path, GAMECORE_DATA=None))
    assert list(json.loads(r.stdout)["installed"]) == ["from-the-install"]


def test_an_unreadable_session_env_is_an_error_and_not_a_guess(tmp_path):
    """session.env is administrators only: guessing the root would write to the wrong one."""
    unreadable = tmp_path / "session.env"
    unreadable.mkdir()   # opening a directory fails like a denied file
    r = _run(tmp_path, "list", env=_env(tmp_path, GAMECORE_DATA=None, GAMECORE_SESSION_ENV=str(unreadable)))
    assert r.returncode != 0
    assert "elevated" in r.stderr


# ── update ───────────────────────────────────────────────────────────────────

def _payload(tmp_path: Path, version: str, names: tuple[str, ...]) -> Path:
    payload = tmp_path / f"payload-{version}"
    for name in names:
        _addon(payload / "gamecore-addons", name, {"version": version, "api": 1})
    return payload


def test_update_stops_every_installed_addon_before_replacing_the_repo(tmp_path):
    """A running addon holds its folder: the swap needs every one of them stopped first."""
    for name in ("one", "two"):
        _addon(tmp_path / "repo", name, {"version": "1.0", "api": 1})
        assert _run(tmp_path, "install", name).returncode == 0
    env = _env(tmp_path, PAYLOAD_DIR=str(_payload(tmp_path, "2.0", ("one", "two"))))
    r = _run(tmp_path, "update", "one", env=env)
    assert r.returncode == 0, r.stderr
    assert _hooks_run(tmp_path)[2:] == ["one uninstall", "two uninstall", "one install", "two install"]
    reg = json.loads((tmp_path / "data" / "config" / "addons.json").read_text(encoding="utf-8"))
    assert {n: e["version"] for n, e in reg.items()} == {"one": "2.0", "two": "2.0"}


def test_a_failed_fetch_restarts_the_addons_it_stopped(tmp_path):
    _addon(tmp_path / "repo", "one", {"version": "1.0", "api": 1})
    assert _run(tmp_path, "install", "one").returncode == 0
    r = _run(tmp_path, "update", env=_env(tmp_path, PAYLOAD_DIR=str(tmp_path / "missing")))
    assert r.returncode != 0
    assert _hooks_run(tmp_path) == ["one install", "one uninstall", "one install"]
    assert (tmp_path / "repo" / "addons" / "one" / "addon.json").exists()


def test_update_fetches_the_archive_and_keeps_nothing_of_the_old_repo(tmp_path):
    """The real download path, from a file:// URL: GitHub's zip has one top folder."""
    _addon(tmp_path / "repo", "one", {"version": "1.0", "api": 1})
    (tmp_path / "repo" / "stale.txt").write_text("old", encoding="utf-8")
    assert _run(tmp_path, "install", "one").returncode == 0
    archive = tmp_path / "main.zip"
    src = _payload(tmp_path, "2.0", ("one",)) / "gamecore-addons"
    with zipfile.ZipFile(archive, "w") as zf:
        for f in src.rglob("*"):
            zf.write(f, "gamecore-addons-windows-main/" + f.relative_to(src).as_posix())
    r = _run(tmp_path, "update", env=_env(tmp_path, OFFLINE="0", GCA_REPO_URL=archive.as_uri()))
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "repo" / "stale.txt").exists()
    assert json.loads((tmp_path / "repo" / "addons" / "one" / "addon.json").read_text())["version"] == "2.0"
    assert not (tmp_path / "repo.new").exists() and not (tmp_path / "repo.old").exists()


def test_an_archive_entry_outside_the_repo_is_refused(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("top/addons/x/addon.json", "{}")
        zf.writestr("top/../../escaped.txt", "x")
    with pytest.raises(gca.AddonError, match="outside"):
        gca.extract_repo(archive, tmp_path / "dest")
    assert not (tmp_path / "escaped.txt").exists()


# ── the addons' own Python ───────────────────────────────────────────────────

def test_addon_python_is_a_copy_in_the_data_root_refreshed_only_when_asked(tmp_path, monkeypatch):
    """Addons must not run on the code root's interpreter: the updater stops those processes."""
    base = tmp_path / "base"
    (base / "Lib" / "site-packages" / "big").mkdir(parents=True)
    (base / "Lib" / "os.py").write_text("", encoding="utf-8")
    (base / "python.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(gca.sys, "base_prefix", str(base))
    ctx = gca.Context({"GAMECORE_PATH": str(tmp_path / "install"), "GAMECORE_DATA": str(tmp_path / "data")})
    home = tmp_path / "data" / "addons" / "_python"

    assert ctx and gca.addon_python(ctx, refresh=False) == home / "python.exe"
    assert (home / "Lib" / "os.py").exists()
    assert list((home / "Lib" / "site-packages").iterdir()) == [], "the backend's packages stay behind"

    (home / gca.PYTHON_STAMP).write_text("3.0.0\n", encoding="utf-8")
    (base / "python.exe").write_text("new", encoding="utf-8")
    gca.addon_python(ctx, refresh=False)
    assert (home / "python.exe").read_text() == "", "running addons hold it: no refresh on install"
    gca.addon_python(ctx, refresh=True)
    assert (home / "python.exe").read_text() == "new"


# ── the shim on PATH ─────────────────────────────────────────────────────────

def test_the_cmd_shim_runs_the_cli_on_the_installations_venv(tmp_path):
    """`gamecore-addon.cmd` from an installed layout: install\bin next to .venv."""
    code = tmp_path / "code"
    (code / "install" / "bin").mkdir(parents=True)
    for name in ("gamecore-addon", "gamecore-addon.cmd"):
        (code / "install" / "bin" / name).write_bytes((CLI.parent / name).read_bytes())
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(code / ".venv")],
                   check=True, timeout=120)
    _registry_with(tmp_path / "data", "from-the-shim")
    env = _env(tmp_path, GAMECORE_PATH=str(code), GAMECORE_DATA=str(tmp_path / "data"))
    r = subprocess.run(["cmd.exe", "/c", str(code / "install" / "bin" / "gamecore-addon.cmd"),
                        "list", "--json"], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    assert list(json.loads(r.stdout)["installed"]) == ["from-the-shim"]


def test_a_failed_swap_puts_the_old_repo_back(tmp_path, monkeypatch):
    """The new copy cannot be renamed in: the box must not end with no repo at all."""
    current, staged, old = tmp_path / "_repo", tmp_path / "_repo.new", tmp_path / "_repo.old"
    (current / "addons").mkdir(parents=True)
    (staged / "addons").mkdir(parents=True)
    real_rename = Path.rename

    def rename(self, target):
        if self == staged:
            raise PermissionError(5, "Access is denied")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(gca.AddonError, match="old one is kept"):
        gca.swap_repo(current, staged, old)
    assert (current / "addons").is_dir() and not old.exists() and not staged.exists()
