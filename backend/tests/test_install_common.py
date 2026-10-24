"""install/lib: config parsing, settings, dry runs, the install record, step selection."""
from __future__ import annotations

import subprocess

from backend.tests.powershell_harness import INSTALL, POWERSHELL, needs_powershell, run_ps, settings_ps

pytestmark = needs_powershell


def test_install_conf_keeps_quoted_values_and_drops_comments(tmp_path):
    result = run_ps(tmp_path, r"""
        $text = "# comment`nUSER_NAME=pavic   # trailing`nWEB_PASSWORD=""p#ss word""`nEMPTY=''`nnot a line`nAPPS='all'"
        $GcTest['config'] = Read-GcConfigText $text
    """)
    assert result["config"] == {"USER_NAME": "pavic", "WEB_PASSWORD": "p#ss word", "EMPTY": "", "APPS": "all"}


def test_the_defaults_are_program_files_and_documents(tmp_path):
    result = run_ps(tmp_path, r"""
        $s = Resolve-GcConfiguration -Config @{} -SourceRoot 'C:\src'
        $GcTest['s'] = @{ code = $s.CodeRoot; data = $s.DataRoot; port = $s.Port; lan = $s.WebPort
            user = $s.ConsoleUser; autologon = $s.Autologon; ssh = $s.Ssh }
        $GcTest['expected'] = @{ code = (Join-Path $env:ProgramFiles 'GameCore'); user = $env:USERNAME }
    """)
    s, expected = result["s"], result["expected"]
    assert (s["code"], s["user"]) == (expected["code"], expected["user"])
    assert s["data"].endswith(r"Documents\GameCore")     # the suite's profile has no known folders
    assert (s["port"], s["lan"], s["autologon"], s["ssh"]) == ("8765", "8443", True, True)


def test_install_conf_overrides_the_defaults(tmp_path):
    result = run_ps(tmp_path, r"""
        $s = Resolve-GcConfiguration -Config @{ GAMECORE_DATA = 'D:\GameCoreData'; AUTOLOGON = '0'; SSH = '0' } -SourceRoot 'C:\src'
        $GcTest['s'] = @{ data = $s.DataRoot; autologon = $s.Autologon; ssh = $s.Ssh }
    """)
    assert result["s"] == {"data": "D:\\GameCoreData", "autologon": False, "ssh": False}


def test_a_port_that_is_not_a_number_is_refused(tmp_path):
    result = run_ps(tmp_path, r"""
        try { Resolve-GcConfiguration -Config @{ WEB_PORT = '87; rm' } -SourceRoot 'C:\src' | Out-Null }
        catch { $GcTest['error'] = $_.Exception.Message }
    """)
    assert "WEB_PORT" in result["error"]


def test_a_dry_run_lists_the_change_and_does_not_make_it(tmp_path):
    result = run_ps(tmp_path, r"""
        Invoke-GcChange 'do the thing' { Note 'ran' $true }
    """, dry_run=True)
    assert result["changes"] == ["do the thing"]
    assert result["calls"] == []


def test_the_record_keeps_the_first_value_across_runs(tmp_path):
    run_ps(tmp_path, "Set-GcRecord 'configure-power' 'HiberbootEnabled' 1")
    result = run_ps(tmp_path, r"""
        Import-GcManifest | Out-Null
        Set-GcRecord 'configure-power' 'HiberbootEnabled' 0
        Set-GcRecord 'configure-power' 'other' $null
    """)
    assert result["manifest"]["configure-power"] == {"HiberbootEnabled": 1, "other": None}
    assert (tmp_path / "state" / "install-manifest.json").exists()


def test_a_dry_run_writes_no_record(tmp_path):
    run_ps(tmp_path, "Set-GcRecord 'configure-power' 'HiberbootEnabled' 1", dry_run=True)
    assert not (tmp_path / "state").exists()


def test_an_unknown_step_name_is_refused_with_the_list(tmp_path):
    result = run_ps(tmp_path, r"""
        try { Select-GcStep -Only 'install-everything' | Out-Null } catch { $GcTest['error'] = $_.Exception.Message }
        $GcTest['some'] = @(Select-GcStep -Only 'configure-power', 'install-caddy' | ForEach-Object { $_.Name })
    """)
    assert "configure-power" in result["error"] and "install-everything" in result["error"]
    assert result["some"] == ["install-caddy", "configure-power"]      # install order, not argument order


def test_a_failing_step_is_reported_and_the_next_one_still_runs(tmp_path):
    result = run_ps(tmp_path, rf"""
        function Install-GcFirewall {{ throw 'no firewall service' }}
        function Install-GcOpenSsh {{ param($Settings) Note 'ssh' $Settings.ConsoleUser }}
        $s = {settings_ps(tmp_path)}
        $ok = foreach ($step in (Select-GcStep -Only 'configure-firewall', 'install-openssh')) {{
            Invoke-GcStep -Step $step -Function $step.Install -Arguments @{{ Settings = $s; Unattended = $true }}
        }}
        $GcTest['ok'] = @($ok)
    """)
    assert result["ok"] == [False, True]
    assert result["warnings"] == ["configure-firewall failed: no firewall service"]
    assert result["calls"] == [{"name": "ssh", "value": "player"}]


def test_every_step_file_defines_its_two_functions(tmp_path):
    result = run_ps(tmp_path, r"""
        $GcTest['missing'] = @(foreach ($step in $script:GcSteps) {
            foreach ($f in $step.Install, $step.Uninstall) { if (-not (Get-Command $f -ErrorAction SilentlyContinue)) { $f } }
        })
        $GcTest['names'] = @($script:GcSteps | ForEach-Object { $_.Name })
    """)
    assert result["missing"] == []
    # A step file nobody runs is a step the install silently skips.
    assert sorted(result["names"]) == sorted(p.stem for p in (INSTALL / "steps").glob("*.ps1"))


def test_every_powershell_file_is_ascii():
    """Windows PowerShell 5.1 reads a file without a BOM as ANSI: one "->" typed as an arrow corrupts a string."""
    offenders = [str(p.relative_to(INSTALL)) for p in INSTALL.rglob("*.ps1")
                 if any(b > 127 for b in p.read_bytes())]
    assert offenders == []


def test_every_powershell_file_parses():
    script = ("$bad = 0; Get-ChildItem -LiteralPath '{root}' -Recurse -Filter *.ps1 | ForEach-Object {{ "
              "$e = $null; [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$null, [ref]$e) | Out-Null; "
              "foreach ($x in $e) {{ $bad++; Write-Output \"$($_.Name):$($x.Extent.StartLineNumber) $($x.Message)\" }} }}; "
              "Write-Output \"errors=$bad\"").format(root=INSTALL)
    out = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("errors=0"), out.stdout + out.stderr


def test_the_example_install_conf_resolves_to_the_defaults(tmp_path):
    result = run_ps(tmp_path, rf"""
        $c = Read-GcConfig -Path '{INSTALL / "install.conf.example"}'
        $s = Resolve-GcConfiguration -Config $c -SourceRoot 'C:\src'
        $GcTest['s'] = @{{ port = $s.Port; lan = $s.WebPort; autologon = $s.Autologon; ssh = $s.Ssh
            code = $s.CodeRoot; password = (Get-GcSetting $c 'WEB_PASSWORD' 'none') }}
    """)
    assert result["s"]["port"] == "8765" and result["s"]["lan"] == "8443"
    assert result["s"]["autologon"] is True and result["s"]["ssh"] is True
    assert result["s"]["code"].endswith(r"\GameCore") and result["s"]["password"] == "none"
