"""Run the PowerShell installer's functions under pytest, with the machine faked.

A test hands a PowerShell snippet. It runs in one script with install/lib and
every install/steps file dot-sourced, after the test's fake functions, which
shadow the real cmdlets (PowerShell resolves a function before a cmdlet of the
same name). `$GcTest` is a hashtable the fakes fill in; the snippet's last word
is `Out-GcResult`, which prints it as JSON for the test to read.

Nothing here touches the machine: the state directory (the manifest,
session.env) is the test's tmp_path, and every system call a step makes goes
through a fake or fails loudly.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "install"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")

needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell is a Windows tool")

PRELUDE = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$InformationPreference = 'SilentlyContinue'
. '{install}\lib\common.ps1'
. '{install}\lib\steps.ps1'
$GcTest = @{{ calls = New-Object System.Collections.Generic.List[object] }}
function Write-Host {{ }}
function Out-GcResult {{
    $GcTest['changes'] = @($script:GcChanges)
    $GcTest['warnings'] = @($script:GcWarnings)
    $GcTest['manifest'] = $script:GcManifest
    '<<<RESULT' + ($GcTest | ConvertTo-Json -Depth 10 -Compress) + 'RESULT>>>'
}}
function Note {{ param([string]$Name, $Value) $GcTest.calls.Add(@{{ name = $Name; value = $Value }}) }}
"""


def settings_ps(tmp_path: Path, **overrides) -> str:
    """A PowerShell expression building install settings rooted in tmp_path."""
    values = {
        "SourceRoot": str(REPO), "CodeRoot": str(tmp_path / "code"), "DataRoot": str(tmp_path / "data"),
        "ConsoleUser": "player", "Port": "8765", "WebPort": "8443", "Autologon": True, "Ssh": True,
    }
    values.update(overrides)
    config = values.pop("Config", {})
    parts = []
    for key, value in values.items():
        if isinstance(value, bool):
            parts.append(f"{key} = ${str(value).lower()}")
        else:
            parts.append(f"{key} = '{value}'")
    conf = "; ".join(f"'{k}' = '{v}'" for k, v in config.items())
    parts.append(f"Config = @{{ {conf} }}")
    return "([pscustomobject]@{ " + "; ".join(parts) + " })"


def run_ps(tmp_path: Path, body: str, *, dry_run: bool = False, timeout: int = 120) -> dict:
    """Run `body` after the prelude; return what Out-GcResult printed."""
    state_dir = tmp_path / "state"
    script = tmp_path / f"case-{len(list(tmp_path.glob('case-*.ps1')))}.ps1"
    text = PRELUDE.format(install=INSTALL)
    if dry_run:
        text += "$script:GcDryRun = $true\n"
    text += body + "\nOut-GcResult\n"
    # A BOM: Windows PowerShell reads a BOM-less script as ANSI.
    script.write_text(text, encoding="utf-8-sig", newline="\n")
    env = {**os.environ, "GAMECORE_STATE_DIR": str(state_dir)}
    out = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-File", str(script)], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=env, timeout=timeout)
    stdout = out.stdout
    if "<<<RESULT" not in stdout:
        raise AssertionError(f"PowerShell failed ({out.returncode}):\n{stdout}\n{out.stderr}")
    payload = stdout.split("<<<RESULT", 1)[1].split("RESULT>>>", 1)[0]
    result = json.loads(payload)
    result["calls"] = result.get("calls") or []
    return result


def calls(result: dict, name: str) -> list:
    """The values noted under `name`, in order."""
    return [c["value"] for c in result["calls"] if c["name"] == name]
