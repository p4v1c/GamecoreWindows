"""Pack installation and layout configuration, with no real input or services."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.services.catalog import load_catalog
from backend.services.installer import applier as ap
from backend.services.installer import host_access as host
from backend.services.installer.providers import Result
from backend.services.configgen import seed

ROOT = Path(__file__).resolve().parents[2]


def module(path):
    spec = importlib.util.spec_from_file_location("layout_test_" + path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def host_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "ETC", tmp_path / "etc")
    monkeypatch.setattr(host, "RECEIPT", tmp_path / "state" / "receipt.json")
    monkeypatch.setattr(host, "PTRACE", tmp_path / "ptrace_scope")
    host.PTRACE.write_text("1\n")
    monkeypatch.setattr(host.os, "geteuid", lambda: 0)
    calls = []

    def run(*args):
        calls.append(args)
        if args[0] == "sysctl":
            host.PTRACE.write_text(args[-1].split("=")[1] + "\n")

    monkeypatch.setattr(host, "_run", run)
    return calls


@pytest.mark.linux_until_port(7)
@pytest.mark.parametrize("emu", ["azahar", "melonds"])
def test_pack_installs_daemon_service_and_seed(tmp_path, monkeypatch, host_sandbox, emu):
    pack = load_catalog(ROOT / "catalog", tmp_path / "none")[emu]
    home = tmp_path / "player"
    home.mkdir()
    ctx = ap.AppContext(gamecore_path=tmp_path / "code", gamecore_data=tmp_path / "data",
                        user_home=home)
    monkeypatch.setattr(ap, "install", lambda *a: Result(True, "fake Flatpak installed"))
    results = ap.apply(pack, ctx)
    assert all(r.ok for r in results), results
    daemon = home / f".local/share/gamecore/layout-toggle/{emu}/{emu}_layout_toggle.py"
    assert daemon.is_file()
    assert daemon.stat().st_mode & 0o111
    unit = ctx.unit_dir / f"{emu}-layout-toggle.service"
    assert unit.is_file()
    assert (ctx.unit_dir / "default.target.wants" / unit.name).resolve() == unit
    assert "@GAMECORE_PATH@" not in unit.read_text()
    target = home / "config"
    seed.deploy(pack.seed_dir, target, home=home, gamecore_path=ctx.gamecore_path)
    if emu == "azahar":
        assert "layouts_to_cycle=0, 1\n" in (target / "qt-config.ini").read_text()
        assert "screen_top_stretch=true" in (target / "qt-config.ini").read_text()
        assert not any(c[0] == "sysctl" for c in host_sandbox)
    else:
        assert "HK_SwapScreenEmphasis = 16777275" in (target / "melonDS.toml").read_text()
        assert "--no-widescreen" in unit.read_text()
        assert host.PTRACE.read_text().strip() == "0"
        # The installed copy must start on its own: its modules import each other.
        run = subprocess.run([sys.executable, str(daemon), "--help"],
                             capture_output=True, text=True)
        assert run.returncode == 0, run.stderr


@pytest.mark.linux_until_port(7)
def test_host_prerequisites_are_idempotent_and_restore_originals(tmp_path, host_sandbox):
    rel = next(iter(host.UINPUT_FILES))
    previous = host.ETC / rel
    previous.parent.mkdir(parents=True)
    previous.write_text("# existed before GameCore\nuinput\n")
    pack = SimpleNamespace(id="melonds", data={"hostAccess": {"uinput": True, "ptrace": True}})
    ctx = ap.AppContext(gamecore_path=tmp_path)
    assert all(r.ok for r in host.apply_host_access(pack, ctx))
    assert all(r.ok for r in host.apply_host_access(pack, ctx))
    assert json.loads(host.RECEIPT.read_text())["ptrace_before"] == "1"
    host.restore()
    assert previous.read_text() == "# existed before GameCore\nuinput\n"
    assert host.PTRACE.read_text().strip() == "1"
    assert not (host.ETC / host.PTRACE_FILE).exists()
    assert not host.RECEIPT.exists()


@pytest.mark.linux_until_port(7)
def test_uninstall_keeps_an_operator_edited_ptrace_policy(tmp_path, host_sandbox):
    pack = SimpleNamespace(id="melonds", data={"hostAccess": {"ptrace": True}})
    host.apply_host_access(pack, ap.AppContext(gamecore_path=tmp_path))
    policy = host.ETC / host.PTRACE_FILE
    policy.write_text("# operator's replacement\nkernel.yama.ptrace_scope = 2\n")
    host.PTRACE.write_text("2\n")
    host.restore()
    assert "= 2" in policy.read_text()
    assert host.PTRACE.read_text().strip() == "2"


@pytest.mark.linux_until_port(7)
def test_interrupted_ptrace_restore_can_be_retried(tmp_path, monkeypatch, host_sandbox):
    pack = SimpleNamespace(id="melonds", data={"hostAccess": {"ptrace": True}})
    host.apply_host_access(pack, ap.AppContext(gamecore_path=tmp_path))
    run = host._run
    monkeypatch.setattr(host, "_run", lambda *a: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        host.restore()
    assert host.RECEIPT.exists()
    monkeypatch.setattr(host, "_run", run)
    host.restore()
    assert host.PTRACE.read_text().strip() == "1"
    assert not host.RECEIPT.exists()


def test_host_access_is_stripped_from_untrusted_packs(tmp_path, monkeypatch):
    monkeypatch.delenv("GAMECORE_TRUST_LOCAL_PACKS", raising=False)
    data = json.loads((ROOT / "catalog/azahar/pack.json").read_text())
    local = tmp_path / "local" / "azahar"
    local.mkdir(parents=True)
    (local / "pack.json").write_text(json.dumps(data))
    pack = load_catalog(ROOT / "catalog", local.parent)["azahar"]
    assert "hostAccess" in pack.stripped
    assert "hostAccess" not in pack.data


@pytest.mark.linux_until_port(7)
def test_provider_uses_named_users_home_not_roots(tmp_path, monkeypatch):
    provider = module(ROOT / "scripts/gamecore-provider.py")
    pack = SimpleNamespace(id="azahar", kind="emulator", data={})
    monkeypatch.setattr(provider, "load_catalog", lambda *a: {"azahar": pack})
    monkeypatch.setattr(provider.pwd, "getpwnam", lambda user: SimpleNamespace(pw_dir=str(tmp_path)))
    seen = []
    monkeypatch.setattr(provider, "apply", lambda p, ctx: seen.append(ctx) or [])
    monkeypatch.setattr(provider, "enabled_units", lambda *a: [])
    monkeypatch.setattr(provider.sys, "argv", ["provider", "install", "azahar", "--user", "player"])
    assert provider.main() == 0
    assert seen[0].user_home == tmp_path


@pytest.mark.linux_until_port(7)
def test_start_services_does_not_reinstall_the_emulator(tmp_path, monkeypatch):
    provider = module(ROOT / "scripts/gamecore-provider.py")
    pack = SimpleNamespace(id="azahar", kind="emulator", data={})
    monkeypatch.setattr(provider, "load_catalog", lambda *a: {"azahar": pack})
    monkeypatch.setattr(provider, "apply", lambda *a: pytest.fail("reinstalled emulator"))
    calls = []
    monkeypatch.setattr(provider, "start_services", lambda p, ctx: calls.append(p.id) or [])
    monkeypatch.setattr(provider.sys, "argv", ["provider", "start-services", "azahar"])
    assert provider.main() == 0
    assert calls == ["azahar"]


@pytest.mark.linux_until_port(7)
def test_service_restart_reloads_the_gaming_users_manager(tmp_path, monkeypatch):
    ctx = ap.AppContext(gamecore_path=tmp_path, user_home=tmp_path, user="player")
    ctx.unit_dir.mkdir(parents=True)
    (ctx.unit_dir / "azahar-layout-toggle.service").write_text("[Service]\n")
    pack = SimpleNamespace(data={"services": [{"unit": "files/azahar-layout-toggle.service", "enable": True}]}, id="azahar")
    monkeypatch.setattr(ap.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=12345))
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda p: True if str(p) == "/run/user/12345/bus" else real_exists(p))
    calls = []
    monkeypatch.setattr(ap, "_run_as_user", lambda *a: calls.append(a) or SimpleNamespace(returncode=0))
    assert all(r.ok for r in ap.start_services(pack, ctx))
    assert calls[0][1] == ["systemctl", "--user", "daemon-reload"]
    assert calls[1][1] == ["systemctl", "--user", "restart", "azahar-layout-toggle.service"]
    assert calls[0][2]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/12345/bus"


@pytest.mark.parametrize("before", ["[Audio]\nvolume=50\n", "[Layout]\nlayouts_to_cycle=0, 1, 2\n[Audio]\nvolume=50\n"])
def test_azahar_config_adds_missing_keys_and_is_idempotent(tmp_path, before):
    config = module(ROOT / "catalog/azahar/files/apply_azahar_config.py")
    path = tmp_path / "qt-config.ini"
    path.write_text(before)
    assert config.apply(str(path))
    assert config.read_state(str(path)) == {"layouts_to_cycle": "0, 1", "screen_top_stretch": "true"}
    assert "volume=50" in path.read_text()
    assert "screen_top_stretch\\default=false" in path.read_text()
    assert config.apply(str(path)) == []


def test_melonds_snapshot_cannot_restore_a_second_layout_trigger():
    generator = module(ROOT / "catalog/melonds/generator.py")
    text = (ROOT / "catalog/melonds/seed/melonDS.toml").read_text()
    block = generator.extract(text).replace("HK_SwapScreenEmphasis = -1", "HK_SwapScreenEmphasis = 7")
    restored = generator.replace(text, block)
    assert "HK_SwapScreenEmphasis = -1" in generator.extract(restored)
    assert "HK_SwapScreenEmphasis = 16777275" in restored
    assert restored == text


def test_melonds_uses_native_config_even_when_stale_flatpak_config_exists(tmp_path, monkeypatch):
    # The daemon's modules import their siblings, as they do when run as a script.
    monkeypatch.syspath_prepend(str(ROOT / "catalog/melonds/files"))
    daemon = module(ROOT / "catalog/melonds/files/melonds_config.py")
    native, flatpak = tmp_path / "native.toml", tmp_path / "flatpak.toml"
    native.touch()
    flatpak.touch()
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/melon").touch()
    monkeypatch.setenv("GAMECORE_PATH", str(tmp_path))
    monkeypatch.delenv("MELONDS_CONFIG", raising=False)
    monkeypatch.setattr(daemon, "MELONDS_TOMLS", [str(flatpak), str(native)])
    assert daemon.melonds_toml() == str(native)
    assert daemon.is_melonds_binary(str(tmp_path / "lib/melon"))
    assert not daemon.is_melonds_binary("/tmp/unrelated/melon")


@pytest.mark.linux_until_port(7)
def test_ui_install_starts_each_daemon_after_its_configuration(tmp_path):
    """Execute the real CLI's install loop; only external effects are stubs."""
    (tmp_path / "catalog").mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "catalog-query.py").write_text("print('azahar\\nmelonds')\n")
    log = tmp_path / "events"
    (scripts / "gamecore-provider.py").write_text(
        "import os,sys\n"
        "with open(os.environ['LAYOUT_TEST_LOG'], 'a') as f:\n"
        " f.write(' '.join(sys.argv[1:3])+'\\n')\n")
    package = tmp_path / "backend/services/catalog"
    package.mkdir(parents=True)
    (package / "merge.py").write_text("def load_removed(root): return set()\ndef save_removed(*args): pass\n")
    source = (ROOT / "install/bin/gamecore-emu").read_text().split("# ── dispatch")[0]
    source += '''
apply_pack() { echo "seed $1" >> "$LAYOUT_TEST_LOG"; }
refresh_grid() { :; }
cmd_install azahar melonds
'''
    result = subprocess.run(["bash"], input=source, text=True, capture_output=True,
                            cwd=tmp_path,
                            env={**os.environ, "GAMECORE_PATH": str(tmp_path),
                                 "LAYOUT_TEST_LOG": str(log)}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "install azahar", "seed azahar", "start-services azahar",
        "install melonds", "seed melonds", "start-services melonds",
    ]


@pytest.mark.linux_until_port(7)
def test_general_installer_collects_emulator_services(tmp_path):
    """Run the actual emulator installation phase, with no package manager."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "catalog-query.py").write_text("print('azahar\\nmelonds')\n")
    (scripts / "gamecore-provider.py").write_text(
        "print('PACK azahar\\nUNIT azahar-layout-toggle.service\\n'"
        "'PACK melonds\\nUNIT melonds-layout-toggle.service')\n")
    source = (ROOT / "install/arch.sh").read_text()
    phase = source[source.index("EMU_FAILED=()"):source.index("  # ── Curated emulator configs")]
    script = '''
set -euo pipefail
MODE=full
EMULATORS=all
USER_NAME=player
USER_HOME=/unused
GAMECORE_DATA="$GAMECORE_PATH"
msg() { :; }; progress() { :; }; ok() { :; }; info() { :; }; warn() { :; }
'''+phase+'\nfi\nprintf "%s\\n" "${RESTART_UNITS[@]}"\n'
    result = subprocess.run(["bash"], input=script, text=True, capture_output=True,
                            env={**os.environ, "GAMECORE_PATH": str(tmp_path)}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["azahar-layout-toggle.service", "melonds-layout-toggle.service"]
