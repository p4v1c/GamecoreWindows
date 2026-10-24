"""Installer steps that change Windows itself: power, firewall, logon task, Caddy, OpenSSH.

Every cmdlet and native program they call is a fake that notes its arguments
(backend/tests/powershell_harness.py); nothing reaches the machine.
"""
from __future__ import annotations

from backend.tests.powershell_harness import calls, needs_powershell, run_ps, settings_ps

pytestmark = needs_powershell

PLAN = "381b4222-f694-41f0-9685-ff5bb260df2e"
BUTTON = "7648efa3-dd9c-4e3e-b566-50f929386280"
WAKE_TIMERS = "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d"
CONSOLE_LOCK = "0e796bdb-100d-47d6-a2d5-f7d2daa51f51"
USB_SUSPEND = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"


def power_fakes(values: dict[tuple[str, str], int], hiberboot=1) -> str:
    """Win32_PowerSettingDataIndex entries for one plan, plus the registry and powercfg."""
    entries = ",".join(
        f"[pscustomobject]@{{ InstanceID = 'Microsoft:PowerSettingDataIndex\\{{{PLAN}}}\\{src}\\{{{setting}}}'; "
        f"SettingIndexValue = {value} }}"
        for (src, setting), value in values.items())
    return rf"""
        function Get-CimInstance {{ param($Namespace, $ClassName) @({entries}) }}
        function Invoke-GcNative {{ param($FilePath, $ArgumentList) Note 'native' "$FilePath $($ArgumentList -join ' ')" }}
        $script:hiberboot = {hiberboot}
        function Get-GcRegistryValue {{ param($Path, $Name) if ($Name -eq 'HiberbootEnabled') {{ $script:hiberboot }} }}
        function Set-GcRegistryValue {{ param($Path, $Name, $Value, $Type) Note 'registry' "$Name=$Value" }}
        function Restore-GcRegistryValue {{ param($Path, $Name, $Previous, $Type) Note 'restore' "$Name=$Previous" }}
    """


ALL_WRONG = {("AC", BUTTON): 3, ("DC", BUTTON): 1, ("AC", WAKE_TIMERS): 1, ("DC", WAKE_TIMERS): 0,
             ("AC", CONSOLE_LOCK): 1, ("DC", CONSOLE_LOCK): 1, ("AC", USB_SUSPEND): 1}
ALL_RIGHT = {("AC", BUTTON): 1, ("DC", BUTTON): 1, ("AC", WAKE_TIMERS): 0, ("DC", WAKE_TIMERS): 0,
             ("AC", CONSOLE_LOCK): 0, ("DC", CONSOLE_LOCK): 0, ("AC", USB_SUSPEND): 0}


def test_power_sets_sleep_button_no_wake_timers_no_password_and_no_usb_suspend(tmp_path):
    result = run_ps(tmp_path, power_fakes(ALL_WRONG) + f"Install-GcPower {settings_ps(tmp_path)}")
    native = calls(result, "native")
    assert f"powercfg.exe /setacvalueindex {PLAN} 4f971e89-eebd-4455-a8de-9e59040e7347 {BUTTON} 1" in native
    assert f"powercfg.exe /setacvalueindex {PLAN} 238c9fa8-0aad-41ed-83f4-97be242c8f20 {WAKE_TIMERS} 0" in native
    assert f"powercfg.exe /setdcvalueindex {PLAN} fea3413e-7e05-4911-9a71-700331f1c294 {CONSOLE_LOCK} 0" in native
    assert f"powercfg.exe /setacvalueindex {PLAN} 2a737441-1930-4402-8d77-b2bebba308a3 {USB_SUSPEND} 0" in native
    assert native[-1] == "powercfg.exe /setactive SCHEME_CURRENT"
    assert len(native) == 5 + 1                      # only the five values that differ, then apply
    assert calls(result, "registry") == ["HiberbootEnabled=0"]


def test_power_notes_every_value_it_replaces(tmp_path):
    result = run_ps(tmp_path, power_fakes(ALL_WRONG) + f"Install-GcPower {settings_ps(tmp_path)}")
    record = result["manifest"]["configure-power"]
    assert record[f"{PLAN}|AC|4f971e89-eebd-4455-a8de-9e59040e7347|{BUTTON}"] == 3
    assert record["HiberbootEnabled"] == 1
    assert len(record) == 6


def test_power_already_right_changes_nothing(tmp_path):
    result = run_ps(tmp_path, power_fakes(ALL_RIGHT, hiberboot=0) + f"Install-GcPower {settings_ps(tmp_path)}")
    assert result["changes"] == []
    assert calls(result, "native") == []


def test_power_uninstall_puts_each_value_back(tmp_path):
    record = (f"@{{ '{PLAN}|AC|4f971e89-eebd-4455-a8de-9e59040e7347|{BUTTON}' = 3; "
              "'HiberbootEnabled' = 1 }")
    result = run_ps(tmp_path, power_fakes(ALL_RIGHT) + f"Uninstall-GcPower {record} {settings_ps(tmp_path)}")
    assert calls(result, "native") == [
        f"powercfg.exe /setacvalueindex {PLAN} 4f971e89-eebd-4455-a8de-9e59040e7347 {BUTTON} 3",
        "powercfg.exe /setactive SCHEME_CURRENT"]
    assert calls(result, "restore") == ["HiberbootEnabled=1"]


FIREWALL_FAKES = r"""
    $script:rules = @{}
    function Get-NetFirewallRule { param($DisplayName, $Name, $ErrorAction)
        if ($DisplayName -and $script:rules.ContainsKey($DisplayName)) { $script:rules[$DisplayName] } }
    function Get-NetFirewallPortFilter { param([Parameter(ValueFromPipeline)]$Rule) process { $Rule.Filter } }
    function New-NetFirewallRule { param($DisplayName, $Direction, $Action, $Protocol, $LocalPort, $Profile, $Group)
        Note 'new' "$DisplayName|$Direction|$Action|$Protocol|$LocalPort|$Profile|$Group" }
    function Remove-NetFirewallRule { param($DisplayName, $ErrorAction) Note 'remove' $DisplayName }
"""


def test_firewall_opens_web_access_and_ssh_on_private_networks_only(tmp_path):
    result = run_ps(tmp_path, FIREWALL_FAKES + f"Install-GcFirewall {settings_ps(tmp_path)}")
    assert calls(result, "new") == ["GameCore - Web access|Inbound|Allow|TCP|8443|Private|GameCore",
                                    "GameCore - SSH|Inbound|Allow|TCP|22|Private|GameCore"]
    assert sorted(result["manifest"]["configure-firewall"]) == ["GameCore - SSH", "GameCore - Web access"]


def test_firewall_without_ssh_opens_only_web_access(tmp_path):
    result = run_ps(tmp_path, FIREWALL_FAKES + f"Install-GcFirewall {settings_ps(tmp_path, Ssh=False)}")
    assert [c.split("|")[0] for c in calls(result, "new")] == ["GameCore - Web access"]


def test_firewall_leaves_a_matching_rule_alone_and_replaces_a_wrong_one(tmp_path):
    result = run_ps(tmp_path, FIREWALL_FAKES + rf"""
        $script:rules['GameCore - Web access'] = [pscustomobject]@{{ Enabled = 'True'; Profile = 'Private'
            Filter = [pscustomobject]@{{ LocalPort = '8443'; Protocol = 'TCP' }} }}
        $script:rules['GameCore - SSH'] = [pscustomobject]@{{ Enabled = 'True'; Profile = 'Any'
            Filter = [pscustomobject]@{{ LocalPort = '22'; Protocol = 'TCP' }} }}
        Install-GcFirewall {settings_ps(tmp_path)}
    """)
    assert calls(result, "remove") == ["GameCore - SSH"]
    assert [c.split("|")[0] for c in calls(result, "new")] == ["GameCore - SSH"]


def test_firewall_uninstall_removes_only_the_rules_it_made(tmp_path):
    result = run_ps(tmp_path, FIREWALL_FAKES +
                    f"Uninstall-GcFirewall @{{ 'GameCore - Web access' = $true }} {settings_ps(tmp_path)}")
    assert calls(result, "remove") == ["GameCore - Web access"]


TASK_FAKES = r"""
    $script:task = $null
    function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) $script:task }
    function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory)
        @{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory } }
    function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User) @{ AtLogOn = [bool]$AtLogOn; User = $User } }
    function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel)
        @{ UserId = $UserId; LogonType = "$LogonType"; RunLevel = "$RunLevel" } }
    function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries,
        $ExecutionTimeLimit, $MultipleInstances, [switch]$StartWhenAvailable)
        @{ Battery = [bool]$AllowStartIfOnBatteries -and [bool]$DontStopIfGoingOnBatteries
           Limit = "$ExecutionTimeLimit"; Instances = "$MultipleInstances" } }
    function Register-ScheduledTask { param($TaskPath, $TaskName, $Action, $Trigger, $Principal, $Settings,
        $Description, [switch]$Force)
        Note 'register' @{ path = "$TaskPath$TaskName"; action = $Action; trigger = $Trigger
            principal = $Principal; settings = $Settings } }
"""


def test_the_session_task_runs_the_launcher_elevated_at_the_console_users_sign_in(tmp_path):
    result = run_ps(tmp_path, TASK_FAKES + f"Install-GcSessionTask {settings_ps(tmp_path)}")
    [task] = calls(result, "register")
    code = str(tmp_path / "code")
    assert task["path"] == "\\GameCore\\Session"
    assert task["action"]["Execute"] == code + "\\.venv\\Scripts\\pythonw.exe"
    assert task["action"]["Arguments"] == f'"{code}\\install\\bin\\gamecore-session"'
    assert task["trigger"]["AtLogOn"] is True and task["trigger"]["User"].endswith("\\player")
    assert task["principal"]["RunLevel"] == "Highest" and task["principal"]["LogonType"] == "Interactive"
    assert task["settings"] == {"Battery": True, "Limit": "00:00:00", "Instances": "IgnoreNew"}


def test_a_session_task_already_right_is_not_registered_again(tmp_path):
    code = tmp_path / "code"
    result = run_ps(tmp_path, TASK_FAKES + rf"""
        $script:task = [pscustomobject]@{{
            Actions = @([pscustomobject]@{{ Execute = '{code}\.venv\Scripts\pythonw.exe'
                Arguments = '"{code}\install\bin\gamecore-session"' }})
            Principal = [pscustomobject]@{{ RunLevel = 'Highest'; UserId = 'PC\player' }} }}
        Install-GcSessionTask {settings_ps(tmp_path)}
    """)
    assert calls(result, "register") == []
    assert result["changes"] == []


CADDY_FAKES = r"""
    $script:service = $null
    $script:version = $null
    function Get-Service { param($Name, $ErrorAction) $script:service }
    function Get-GcInstalledCaddyVersion { param($Exe) $script:version }
    function Install-GcCaddyBinary { param($Exe) Note 'download' $Exe; $script:version = $script:GcCaddyVersion }
    function New-Service { param($Name, $BinaryPathName, $DisplayName, $Description, $StartupType)
        Note 'service' @{ name = $Name; bin = $BinaryPathName; start = "$StartupType" }
        $script:service = [pscustomobject]@{ Status = 'Stopped' } }
    function Invoke-GcNative { param($FilePath, $ArgumentList) Note 'native' "$FilePath $($ArgumentList -join ' ')" }
    function Get-GcRegistryValue { param($Path, $Name) $null }
    function Set-GcRegistryValue { param($Path, $Name, $Value, $Type) Note 'env' @{ path = $Path; value = @($Value); type = $Type } }
    function Restart-Service { param($Name, [switch]$Force) Note 'restart' $Name }
    function Stop-Service { param($Name, [switch]$Force, $ErrorAction) Note 'stop' $Name }
"""


def test_caddy_is_a_service_running_the_shipped_caddyfile(tmp_path):
    result = run_ps(tmp_path, CADDY_FAKES + f"Install-GcCaddy {settings_ps(tmp_path)}")
    code = str(tmp_path / "code")
    assert calls(result, "download") == [code + "\\caddy\\caddy.exe"]
    [service] = calls(result, "service")
    assert service["name"] == "GameCoreCaddy" and service["start"] == "Automatic"
    assert service["bin"] == (f'"{code}\\caddy\\caddy.exe" run --config '
                              f'"{code}\\install\\system\\Caddyfile" --adapter caddyfile')
    assert "sc.exe failure GameCoreCaddy reset= 86400 actions= restart/5000" in calls(result, "native")
    [env] = calls(result, "env")
    assert env["path"].endswith("\\Services\\GameCoreCaddy") and env["type"] == "MultiString"
    state = str(tmp_path / "state" / "caddy")
    assert env["value"] == [f"XDG_DATA_HOME={state}", f"XDG_CONFIG_HOME={state}"]
    assert calls(result, "restart") == ["GameCoreCaddy"]


def test_caddy_at_the_pinned_version_is_not_downloaded_again(tmp_path):
    result = run_ps(tmp_path, CADDY_FAKES + rf"""
        $script:version = $script:GcCaddyVersion
        $script:service = [pscustomobject]@{{ Status = 'Running' }}
        $paths = Get-GcCaddyPaths '{tmp_path / "code"}'
        function Get-GcRegistryValue {{ param($Path, $Name) Get-GcCaddyEnvironment $paths }}
        Install-GcCaddy {settings_ps(tmp_path)}
    """)
    assert result["changes"] == []


def test_the_caddyfile_points_the_ca_download_at_the_service_data_folder():
    from backend.tests.powershell_harness import INSTALL
    text = (INSTALL / "system" / "Caddyfile").read_text(encoding="utf-8")
    assert "root * {$XDG_DATA_HOME}/caddy/pki/authorities/local" in text
    assert "/var/lib/caddy" not in text


SSH_FAKES = r"""
    $script:capability = 'NotPresent'
    $script:sshd = $null
    $script:rule = [pscustomobject]@{ Enabled = 'True' }
    function Get-WindowsCapability { param([switch]$Online, $Name) [pscustomobject]@{ State = $script:capability } }
    function Add-WindowsCapability { param([switch]$Online, $Name) Note 'add' $Name
        $script:sshd = [pscustomobject]@{ StartType = 'Manual'; Status = 'Stopped' } }
    function Get-Service { param($Name, $ErrorAction) $script:sshd }
    function Set-Service { param($Name, $StartupType) Note 'start-type' "$StartupType" }
    function Start-Service { param($Name) Note 'start' $Name }
    function Get-NetFirewallRule { param($Name, $ErrorAction) $script:rule }
    function Disable-NetFirewallRule { param($Name) Note 'disable' $Name }
"""


def test_openssh_is_installed_started_with_windows_and_private_only(tmp_path):
    result = run_ps(tmp_path, SSH_FAKES + f"Install-GcOpenSsh {settings_ps(tmp_path)}")
    assert calls(result, "add") == ["OpenSSH.Server~~~~0.0.1.0"]
    assert calls(result, "start-type") == ["Automatic"]
    assert calls(result, "start") == ["sshd"]
    assert calls(result, "disable") == ["OpenSSH-Server-In-TCP"]
    assert result["manifest"]["install-openssh"]["capability"] == "OpenSSH.Server~~~~0.0.1.0"


def test_ssh_off_installs_nothing(tmp_path):
    result = run_ps(tmp_path, SSH_FAKES + f"Install-GcOpenSsh {settings_ps(tmp_path, Ssh=False)}")
    assert result["calls"] == [] and result["changes"] == []


def test_an_openssh_the_owner_had_is_not_removed_by_uninstall(tmp_path):
    result = run_ps(tmp_path, SSH_FAKES + r"""
        function Remove-WindowsCapability { param([switch]$Online, $Name) Note 'remove' $Name }
        function Enable-NetFirewallRule { param($Name, $ErrorAction) Note 'enable' $Name }
        Uninstall-GcOpenSsh @{ startType = 'Manual'; windowsRuleEnabled = $true } $null
    """.replace("$null", settings_ps(tmp_path)))
    assert calls(result, "remove") == []
    assert calls(result, "start-type") == ["Manual"]
    assert calls(result, "enable") == ["OpenSSH-Server-In-TCP"]
