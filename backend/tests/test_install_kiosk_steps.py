"""Installer steps for the kiosk boot: GameCore as the console user's shell, and the quiet boot.

The registry, bcdedit, the task's security descriptor and the user hive are
fakes that note their arguments (backend/tests/powershell_harness.py).
"""
from __future__ import annotations

from backend.tests.powershell_harness import calls, needs_powershell, run_ps, settings_ps

pytestmark = needs_powershell

SID = "S-1-5-21-1-2-3-1002"
WINLOGON = rf"Registry::HKEY_USERS\{SID}\Software\Microsoft\Windows NT\CurrentVersion\Winlogon"

SHELL_FAKES = rf"""
    $script:reg = @{{}}
    $script:sddl = 'D:(A;;FA;;;BA)(A;;FA;;;SY)'
    function Get-GcUserSid {{ param($User) '{SID}' }}
    function Test-Path {{ param($LiteralPath) $true }}
    function Get-GcRegistryValue {{ param($Path, $Name) $script:reg["$Path|$Name"] }}
    function Set-GcRegistryValue {{ param($Path, $Name, $Value, $Type) Note 'set' "$Path|$Name=$Value"; $script:reg["$Path|$Name"] = $Value }}
    function Restore-GcRegistryValue {{ param($Path, $Name, $Previous, $Type) Note 'restore' "$Name=$Previous" }}
    function Get-GcTaskSddl {{ $script:sddl }}
    function Set-GcTaskSddl {{ param($Sddl) Note 'sddl' $Sddl; $script:sddl = $Sddl }}
"""


def _shell_command(tmp_path):
    code = tmp_path / "code"
    return f'"{code}\\.venv\\Scripts\\pythonw.exe" "{code}\\install\\bin\\gamecore-shell"'


def test_gamecore_becomes_the_console_users_shell_and_the_old_value_is_recorded(tmp_path):
    result = run_ps(tmp_path, SHELL_FAKES + f"Install-GcKioskShell {settings_ps(tmp_path)}")
    assert calls(result, "set") == [f"{WINLOGON}|Shell={_shell_command(tmp_path)}"]
    assert result["manifest"]["set-kiosk-shell"] == {"sid": SID, "Shell": None}


def test_the_console_user_may_start_the_session_task(tmp_path):
    result = run_ps(tmp_path, SHELL_FAKES + f"Install-GcKioskShell {settings_ps(tmp_path)}")
    assert calls(result, "sddl") == [f"D:(A;;FA;;;BA)(A;;FA;;;SY)(A;;FRFX;;;{SID})"]


def test_a_second_run_changes_nothing(tmp_path):
    result = run_ps(tmp_path, SHELL_FAKES + f"""
        Install-GcKioskShell {settings_ps(tmp_path)}
        $GcTest.calls.Clear()
        Install-GcKioskShell {settings_ps(tmp_path)}
    """)
    assert result["calls"] == []


def test_kiosk_off_leaves_explorer_as_the_shell(tmp_path):
    result = run_ps(tmp_path, SHELL_FAKES + f"Install-GcKioskShell {settings_ps(tmp_path, Kiosk=False)}")
    assert result["calls"] == [] and "set-kiosk-shell" not in result["manifest"]


def test_a_dry_run_of_a_first_install_lists_the_task_grant_without_a_task(tmp_path):
    result = run_ps(tmp_path, SHELL_FAKES + f"""
        function Get-GcTaskSddl {{ throw 'The system cannot find the file specified' }}
        Install-GcKioskShell {settings_ps(tmp_path)}
    """, dry_run=True)
    assert any("start the \\GameCore\\Session task" in c for c in result["changes"])
    assert result["calls"] == []


def test_uninstall_puts_explorer_back_and_drops_a_leftover_rearm(tmp_path):
    result = run_ps(tmp_path, SHELL_FAKES + f"Uninstall-GcKioskShell @{{ sid = '{SID}'; Shell = $null }}")
    assert calls(result, "restore") == ["Shell=", "ShellRearm="]


def test_a_sid_already_in_the_dacl_is_not_added_again(tmp_path):
    result = run_ps(tmp_path, f"$GcTest['sddl'] = Add-GcSddlAce -Sddl 'D:(A;;FA;;;{SID})' -Sid '{SID}'")
    assert result["sddl"] == f"D:(A;;FA;;;{SID})"


# -- quiet boot -----------------------------------------------------------------

BOOT_FAKES = r"""
    $script:reg = @{ 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion|EditionID' = 'Core' }
    $script:bcd = @('Windows Boot Loader', '-------------------', 'identifier              {current}', 'quietboot               No')
    function Get-GcRegistryValue { param($Path, $Name) $script:reg["$Path|$Name"] }
    function Set-GcRegistryValue { param($Path, $Name, $Value, $Type) Note 'set' "$Name=$Value" }
    function Restore-GcRegistryValue { param($Path, $Name, $Previous, $Type) Note 'restore' "$Name=$Previous" }
    function Invoke-GcNative { param($FilePath, $ArgumentList)
        if ($ArgumentList[0] -eq '/enum') { return $script:bcd }
        Note 'bcdedit' ($ArgumentList -join ' ') }
"""


def test_quiet_boot_hides_the_logo_and_the_sign_in_text_and_records_what_was_there(tmp_path):
    result = run_ps(tmp_path, BOOT_FAKES + f"Install-GcQuietBoot {settings_ps(tmp_path)}")
    assert calls(result, "bcdedit") == ["/set {current} bootuxdisabled on"]
    assert calls(result, "set") == ["EnableFirstLogonAnimation=0", "DisableStatusMessages=1",
                                    "DisableLogonBackgroundImage=1"]
    record = result["manifest"]["configure-boot-visuals"]
    assert record["bootuxdisabled"] is None and len(record) == 4


def test_on_home_the_lock_screen_policy_is_not_written_and_it_says_why(tmp_path):
    result = run_ps(tmp_path, BOOT_FAKES + f"Install-GcQuietBoot {settings_ps(tmp_path)}")
    assert not any(c.startswith("NoLockScreen") for c in calls(result, "set"))


def test_on_enterprise_the_lock_screen_goes_too(tmp_path):
    result = run_ps(tmp_path, BOOT_FAKES + rf"""
        $script:reg['HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion|EditionID'] = 'Enterprise'
        Install-GcQuietBoot {settings_ps(tmp_path)}
    """)
    assert "NoLockScreen=1" in calls(result, "set")


def test_a_boot_logo_already_off_in_french_is_left_alone(tmp_path):
    result = run_ps(tmp_path, BOOT_FAKES + rf"""
        $script:bcd += 'bootuxdisabled          Oui'
        Install-GcQuietBoot {settings_ps(tmp_path)}
    """)
    assert calls(result, "bcdedit") == []


def test_quiet_boot_off_changes_nothing(tmp_path):
    result = run_ps(tmp_path, BOOT_FAKES + f"Install-GcQuietBoot {settings_ps(tmp_path, QuietBoot=False)}")
    assert result["calls"] == []


def test_uninstall_deletes_a_value_that_was_absent_and_restores_the_policies(tmp_path):
    result = run_ps(tmp_path, BOOT_FAKES + r"""
        Uninstall-GcQuietBoot @{ bootuxdisabled = $null
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\DisableStatusMessages' = 0 }
    """)
    assert calls(result, "bcdedit") == ["/deletevalue {current} bootuxdisabled"]
    assert calls(result, "restore") == ["DisableStatusMessages=0"]


def test_uninstall_turns_a_translated_off_back_into_off(tmp_path):
    result = run_ps(tmp_path, BOOT_FAKES + "Uninstall-GcQuietBoot @{ bootuxdisabled = 'Non' }")
    assert calls(result, "bcdedit") == ["/set {current} bootuxdisabled off"]
