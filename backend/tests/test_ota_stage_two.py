"""update/apply-update.ps1, stage 2: the swap, the restart and the way back.

The folders are real (tmp_path): every rename, carry and rollback happens on
disk. What would touch the PC is faked by functions that shadow the cmdlets:
processes, the Caddy service, the logon task, the readiness probe, pip.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from backend.tests.powershell_harness import POWERSHELL, needs_powershell

pytestmark = needs_powershell

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "update" / "apply-update.ps1"

FAKES = r"""
$GcTest = @{ calls = New-Object System.Collections.Generic.List[object]; alive = $true; pipFails = $false
    processes = New-Object System.Collections.Generic.List[object]; caddy = 'Running'; task = $true }
function Note { param([string]$Name, $Value) $GcTest.calls.Add(@{ name = $Name; value = $Value }) }
function Start-Sleep { }
function Get-Process { $GcTest.processes.ToArray() }
function Stop-Process { param($Id, [switch]$Force)
    Note 'stop' $Id
    foreach ($p in @($GcTest.processes.ToArray())) { if ($p.Id -eq $Id) { [void]$GcTest.processes.Remove($p) } } }
function Get-Service { param($Name)
    if ($GcTest.caddy) { [pscustomobject]@{ Name = $Name; Status = $GcTest.caddy } } }
function Stop-Service { param($Name, [switch]$Force) Note 'stop-service' $Name; $GcTest.caddy = 'Stopped' }
function Start-Service { param($Name) Note 'start-service' $Name; $GcTest.caddy = 'Running' }
function Get-ScheduledTask { param($TaskPath, $TaskName)
    if ($GcTest.task) { [pscustomobject]@{ TaskPath = $TaskPath; TaskName = $TaskName } } }
function Start-ScheduledTask { param($TaskPath, $TaskName) Note 'start-task' "$TaskPath$TaskName" }
function Start-Process { param($FilePath, $ArgumentList, $WorkingDirectory) Note 'start-process' $FilePath }
function Test-GcUpdateAlive { param($Port) Note 'probe' $Port; return [bool]$GcTest.alive }
function Invoke-GcUpdateNative { param($FilePath, [string[]]$ArgumentList)
    Note 'native' ((@(Split-Path -Leaf $FilePath) + $ArgumentList) -join ' ')
    if ($GcTest.pipFails -and $ArgumentList -contains 'pip') { throw 'pip exited with 1' } }
function Out-GcResult {
    $GcTest.Remove('processes')
    '<<<RESULT' + ($GcTest | ConvertTo-Json -Depth 6 -Compress) + 'RESULT>>>' }
"""


def run_stage_two(tmp_path: Path, body: str) -> dict:
    script = tmp_path / f"case-{len(list(tmp_path.glob('case-*.ps1')))}.ps1"
    text = ("Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n"
            f". '{SCRIPT}'\n" + FAKES + body + "\nOut-GcResult\n")
    script.write_text(text, encoding="utf-8-sig", newline="\n")
    out = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-File", str(script)], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120, env=dict(os.environ))
    if "<<<RESULT" not in out.stdout:
        raise AssertionError(f"PowerShell failed ({out.returncode}):\n{out.stdout}\n{out.stderr}")
    result = json.loads(out.stdout.split("<<<RESULT", 1)[1].split("RESULT>>>", 1)[0])
    result["calls"] = result.get("calls") or []
    return result


def calls(result: dict, name: str) -> list:
    return [c["value"] for c in result["calls"] if c["name"] == name]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_box(tmp_path: Path, *, requirements_changed: bool = False) -> dict:
    """v1 installed with Python, venv and Caddy; v2 unpacked beside it; the plan stage 1 writes."""
    code = tmp_path / "GameCore"
    write(code / "VERSION", "v0.1.0")
    write(code / "backend" / "requirements.txt", "fastapi\n")
    write(code / "old-only.txt", "removed by v2")
    write(code / "python" / "tools" / "python.exe", "interpreter")
    write(code / ".venv" / "Scripts" / "python.exe", "venv")
    write(code / "caddy" / "caddy.exe", "caddy")
    staging = tmp_path / "GameCore.new"
    write(staging / "VERSION", "v0.2.0")
    write(staging / "backend" / "requirements.txt", "fastapi\nnew-dep\n" if requirements_changed else "fastapi\n")
    write(staging / "update" / "windows.py", "")
    import hashlib
    stamp = hashlib.sha256((code / "backend" / "requirements.txt").read_bytes()).hexdigest().upper()
    write(code / ".venv" / "gamecore-requirements.sha256", stamp)
    work = tmp_path / "work"
    work.mkdir()
    plan = {
        "code_root": str(code), "staging": str(staging), "previous": str(tmp_path / "GameCore.prev"),
        "failed": str(tmp_path / "GameCore.failed"), "data_root": str(tmp_path / "data"), "port": "8799",
        "from": "v0.1.0", "to": "v0.2.0", "carry": ["python", ".venv", "caddy"],
        "task_path": "\\GameCore\\", "task_name": "Session", "caddy_service": "GameCoreCaddy",
        "lock": str(work / "update.lock"), "result": str(work / "last-update.json"),
        "log": str(tmp_path / "data" / "logs" / "update" / "update.log"),
    }
    (work / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return {"code": code, "staging": staging, "prev": tmp_path / "GameCore.prev",
            "failed": tmp_path / "GameCore.failed", "work": work, "plan": work / "plan.json"}


def processes_in(code: Path) -> str:
    return (f"$GcTest.processes.Add([pscustomobject]@{{ Id = 11; Name = 'GameCore'; Path = '{code}\\shell\\GameCore.exe' }})\n"
            f"$GcTest.processes.Add([pscustomobject]@{{ Id = 12; Name = 'python'; Path = '{code}\\.venv\\Scripts\\python.exe' }})\n"
            f"$GcTest.processes.Add([pscustomobject]@{{ Id = 10; Name = 'pythonw'; Path = '{code}\\.venv\\Scripts\\pythonw.exe' }})\n"
            "$GcTest.processes.Add([pscustomobject]@{ Id = 99; Name = 'explorer'; Path = 'C:\\Windows\\explorer.exe' })\n")


def apply(box: dict, extra: str = "") -> str:
    return (processes_in(box["code"]) + extra +
            f"$p = [IO.File]::ReadAllText('{box['plan']}') | ConvertFrom-Json\n"
            "Initialize-GcUpdateLog $p.log\nInvoke-GcUpdate $p\n")


def result_of(box: dict) -> dict:
    return json.loads((box["work"] / "last-update.json").read_text(encoding="utf-8-sig"))


def test_a_good_release_is_swapped_in_with_the_installed_pieces_carried_over(tmp_path):
    box = make_box(tmp_path)
    result = run_stage_two(tmp_path, apply(box))
    code = box["code"]
    assert (code / "VERSION").read_text() == "v0.2.0"
    assert not (code / "old-only.txt").exists() and not box["staging"].exists()
    for carried in ("python/tools/python.exe", ".venv/Scripts/python.exe", "caddy/caddy.exe"):
        assert (code / carried).is_file(), carried
        assert not (box["prev"] / carried).exists()
    assert (box["prev"] / "VERSION").read_text() == "v0.1.0"
    # The supervisor first, only GameCore's processes, then Caddy; restarted through the task.
    assert calls(result, "stop") == [10, 11, 12]
    assert calls(result, "stop-service") == ["GameCoreCaddy"] and calls(result, "start-service") == ["GameCoreCaddy"]
    assert calls(result, "start-task") == ["\\GameCore\\Session"]
    assert calls(result, "probe") == ["8799"]
    # Same requirements: no pip; themes and catalogue refreshed with the new code's venv.
    natives = calls(result, "native")
    assert not any("pip" in n for n in natives)
    assert any(n.startswith("python.exe ") and " finish --root " in n and str(code) in n for n in natives)
    assert result_of(box)["result"] == "installed"
    assert not Path(json.loads(box["plan"].read_text())["lock"]).exists()
    log = (tmp_path / "data" / "logs" / "update" / "update.log").read_text()
    assert "code root swapped" in log and "result: installed" in log


def test_changed_requirements_run_pip_in_the_carried_venv(tmp_path):
    box = make_box(tmp_path, requirements_changed=True)
    result = run_stage_two(tmp_path, apply(box))
    pip = [n for n in calls(result, "native") if " pip " in n]
    assert pip and str(box["code"] / "backend" / "requirements.txt") in pip[0]
    stamp = (box["code"] / ".venv" / "gamecore-requirements.sha256").read_text()
    import hashlib
    assert stamp == hashlib.sha256((box["code"] / "backend" / "requirements.txt").read_bytes()).hexdigest().upper()


def test_a_release_that_never_answers_is_rolled_back(tmp_path):
    box = make_box(tmp_path)
    extra = "function Test-GcUpdateAlive { param($Port) Note 'probe' $Port; $GcTest.probes++; return ($GcTest.probes -gt 1) }\n$GcTest.probes = 0\n$script:GcUpdateReadyTimeoutS = 0\n"
    run_stage_two(tmp_path, apply(box, extra))
    code = box["code"]
    assert (code / "VERSION").read_text() == "v0.1.0"
    assert (code / "old-only.txt").exists()
    for carried in ("python/tools/python.exe", ".venv/Scripts/python.exe", "caddy/caddy.exe"):
        assert (code / carried).is_file(), carried
    assert (box["failed"] / "VERSION").read_text() == "v0.2.0"
    assert not box["prev"].exists()
    record = result_of(box)
    assert record["result"] == "rolled-back" and "did not answer" in record["detail"]


def test_a_failed_pip_puts_the_previous_version_back(tmp_path):
    box = make_box(tmp_path, requirements_changed=True)
    result = run_stage_two(tmp_path, apply(box, "$GcTest.pipFails = $true\n"))
    assert (box["code"] / "VERSION").read_text() == "v0.1.0"
    assert (box["code"] / ".venv" / "Scripts" / "python.exe").is_file()
    assert result_of(box)["result"] == "rolled-back"
    assert calls(result, "start-task") == ["\\GameCore\\Session"]     # the old one, started again


def test_a_process_that_will_not_stop_aborts_before_anything_moves(tmp_path):
    box = make_box(tmp_path)
    extra = ("function Stop-Process { param($Id, [switch]$Force) Note 'stop' $Id }\n"
             "$script:GcUpdateStopTimeoutS = 0\n")
    result = run_stage_two(tmp_path, apply(box, extra))
    assert (box["code"] / "VERSION").read_text() == "v0.1.0"
    assert box["staging"].is_dir() and not box["prev"].exists()
    assert result_of(box)["result"] == "failed"
    assert calls(result, "start-task") == ["\\GameCore\\Session"]


def test_a_staging_folder_that_cannot_move_restores_the_code_root(tmp_path):
    box = make_box(tmp_path)
    extra = (f"$GcStaging = '{box['staging']}'\n"
             "function Move-GcUpdateDirectory { param($From, $To)\n"
             "    if ($From -eq $GcStaging) { throw 'locked' }\n"
             "    Move-Item -LiteralPath $From -Destination $To }\n")
    run_stage_two(tmp_path, apply(box, extra))
    assert (box["code"] / "VERSION").read_text() == "v0.1.0"
    assert (box["code"] / "python" / "tools" / "python.exe").is_file()
    assert result_of(box)["result"] == "failed"


def test_an_old_previous_version_is_replaced_by_the_one_just_installed_over(tmp_path):
    box = make_box(tmp_path)
    write(box["prev"] / "VERSION", "v0.0.9")
    run_stage_two(tmp_path, apply(box))
    assert (box["prev"] / "VERSION").read_text() == "v0.1.0"


def test_without_a_logon_task_the_session_starts_directly(tmp_path):
    box = make_box(tmp_path)
    write(box["staging"] / ".venv" / "Scripts" / "pythonw.exe", "")       # shipped venv is never there,
    result = run_stage_two(tmp_path, apply(box, "$GcTest.task = $false\n"))  # but the carried one is
    assert calls(result, "start-task") == []
    assert calls(result, "start-process") == [str(box["code"] / ".venv" / "Scripts" / "pythonw.exe")]


def test_rollback_by_hand_returns_to_the_previous_version(tmp_path):
    box = make_box(tmp_path)
    run_stage_two(tmp_path, apply(box))
    env_file = tmp_path / "state" / "session.env"
    write(env_file, f"GAMECORE_PATH={box['code']}\nGAMECORE_DATA={tmp_path / 'data'}\nGAMECORE_BACKEND_PORT=8798\n")
    result = run_stage_two(tmp_path, processes_in(box["code"]) +
                           f"$p = Get-GcRollbackPlan '{box['code']}' '{env_file}'\n"
                           "New-Item -ItemType Directory -Force -Path (Split-Path -Parent $p.result) | Out-Null\n"
                           "Initialize-GcUpdateLog $p.log\nInvoke-GcUpdateRollback $p\n")
    assert (box["code"] / "VERSION").read_text() == "v0.1.0"
    assert (box["code"] / ".venv" / "Scripts" / "python.exe").is_file()
    assert (box["failed"] / "VERSION").read_text() == "v0.2.0"
    assert calls(result, "probe") == ["8798"]
    record = json.loads((tmp_path / "state" / "update" / "last-update.json").read_text(encoding="utf-8-sig"))
    assert record["result"] == "rolled-back"


def test_the_script_is_ascii_for_windows_powershell():
    """PowerShell 5.1 reads a BOM-less script as ANSI; stage 1 copies it as is."""
    SCRIPT.read_bytes().decode("ascii")


def test_a_warning_on_stderr_is_logged_not_fatal(tmp_path):
    """A pip warning on stderr threw under 'Stop' and rolled a good update back."""
    log = tmp_path / "native.log"
    result = run_stage_two(tmp_path, f". '{SCRIPT}'\n"      # the real Invoke-GcUpdateNative again
                           f"Initialize-GcUpdateLog '{log}'\n"
                           f"Invoke-GcUpdateNative '{POWERSHELL}' @('-NoProfile', '-Command', "
                           "\"[Console]::Error.WriteLine('a warning'); 'done'\")\n"
                           "$GcTest['ok'] = $true\n")
    assert result["ok"] is True
    text = log.read_text()
    assert "a warning" in text and "done" in text


def test_the_script_parses():
    out = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
                          f"$e = $null; [System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}', [ref]$null, [ref]$e) | Out-Null; $e.Count"],
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "0", out.stdout + out.stderr
