"""install.ps1 and uninstall.ps1 end to end, in dry-run mode.

A dry run reads the real machine (power plans, tasks, firewall) and changes
nothing; these tests check that promise on the PC running them, then the step
order and the summary.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from backend.tests.powershell_harness import INSTALL, POWERSHELL, needs_powershell

pytestmark = needs_powershell


def _run(script: str, *args: str, state_dir, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {**os.environ, "GAMECORE_STATE_DIR": str(state_dir)}
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(INSTALL / script), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)


def _headings(stdout: str) -> list[str]:
    return re.findall(r"^== ([a-z-]+)$", stdout, flags=re.M)


def test_a_dry_run_plans_every_step_and_writes_nothing(tmp_path):
    out = _run("install.ps1", "-DryRun", "-CodeRoot", str(tmp_path / "code"),
               "-DataRoot", str(tmp_path / "data"), "-ConsoleUser", "player", state_dir=tmp_path / "state")
    assert "== Summary: would change" in out.stdout, out.stdout + out.stderr
    assert _headings(out.stdout) == [
        "copy-code-root", "install-python", "create-data-root", "write-session-env", "set-web-password",
        "install-caddy", "configure-firewall", "install-openssh", "configure-power", "set-wifi-location",
        "configure-autologon", "register-session-task", "set-kiosk-shell", "configure-boot-visuals",
        "enable-device-lockdown", "build-media-index"]
    assert "would: copy" in out.stdout and "would: register the \\GameCore\\Session logon task" in out.stdout
    assert list(tmp_path.iterdir()) == [], "a dry run created files"
    assert out.returncode == 0, out.stdout


def test_a_dry_run_of_one_step_runs_only_that_step(tmp_path):
    out = _run("install.ps1", "-DryRun", "-Only", "configure-firewall", "-DataRoot", str(tmp_path / "d"),
               state_dir=tmp_path / "state")
    assert _headings(out.stdout) == ["configure-firewall"], out.stdout + out.stderr


def test_an_unknown_step_stops_before_anything_runs(tmp_path):
    out = _run("install.ps1", "-DryRun", "-Only", "format-disk", state_dir=tmp_path / "state")
    assert out.returncode != 0
    assert "unknown step 'format-disk'" in out.stdout + out.stderr
    assert _headings(out.stdout) == []


def test_uninstall_without_a_record_refuses(tmp_path):
    out = _run("uninstall.ps1", "-DryRun", state_dir=tmp_path / "state")
    assert out.returncode != 0 and "no install record" in out.stdout + out.stderr


def test_uninstall_runs_the_steps_backwards_and_keeps_the_record_in_a_dry_run(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    manifest = {"settings": {"CodeRoot": str(tmp_path / "code"), "DataRoot": str(tmp_path / "data"),
                             "ConsoleUser": "player"},
                "copy-code-root": {"created": str(tmp_path / "code")}}
    (state / "install-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "code").mkdir()
    out = _run("uninstall.ps1", "-DryRun", state_dir=state)
    headings = _headings(out.stdout)
    assert headings[0] == "build-media-index" and headings[-1] == "copy-code-root", out.stdout + out.stderr
    assert headings.index("register-session-task") < headings.index("copy-code-root")
    assert f"would: remove {tmp_path / 'code'}" in out.stdout
    assert "Your data is still in" in out.stdout
    assert (tmp_path / "code").is_dir() and (state / "install-manifest.json").exists()
