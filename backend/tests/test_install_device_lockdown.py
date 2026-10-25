"""Installer step enable-device-lockdown: Custom Logon and Unbranded Boot where the edition has them.

The edition, the optional-feature cmdlets, the registry and bcdedit are fakes
that note their arguments (backend/tests/powershell_harness.py).
"""
from __future__ import annotations

from backend.tests.powershell_harness import calls, needs_powershell, run_ps, settings_ps

pytestmark = needs_powershell

EDITION = r"HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion|EditionID"
EMBEDDED = r"HKLM:\SOFTWARE\Microsoft\Windows Embedded\EmbeddedLogon"
LOGONUI = r"HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI"
FEATURES = ("Client-DeviceLockdown", "Client-EmbeddedLogon", "Client-EmbeddedBootExp")
CUSTOM_LOGON_SETS = ["HideAutoLogonUI=1", "HideFirstLogonAnimation=1", "BrandingNeutral=33", "AnimationDisabled=1"]


def fakes(edition: str = "Enterprise", features: dict[str, str] | None = None) -> str:
    """Fakes for a PC of `edition` whose optional features have the given states (absent = unknown to Windows)."""
    if features is None:
        features = dict.fromkeys(FEATURES, "Disabled")
    table = "; ".join(f"'{name}' = '{state}'" for name, state in features.items())
    return rf"""
    $script:reg = @{{ '{EDITION}' = '{edition}' }}
    $script:features = @{{ {table} }}
    $script:bcd = @('Windows Boot Manager', 'identifier              {{globalsettings}}')
    function Get-GcRegistryValue {{ param($Path, $Name) $script:reg["$Path|$Name"] }}
    function Set-GcRegistryValue {{ param($Path, $Name, $Value, $Type) Note 'set' "$Name=$Value"; $script:reg["$Path|$Name"] = $Value }}
    function Restore-GcRegistryValue {{ param($Path, $Name, $Previous, $Type) Note 'restore' "$Name=$Previous" }}
    function Get-WindowsOptionalFeature {{ param([switch]$Online, $FeatureName)
        if (-not $script:features.ContainsKey($FeatureName)) {{ throw "Feature name $FeatureName is unknown." }}
        [pscustomobject]@{{ FeatureName = $FeatureName; State = $script:features[$FeatureName] }} }}
    function Enable-WindowsOptionalFeature {{ param([switch]$Online, $FeatureName, [switch]$NoRestart)
        Note 'enable' "$FeatureName|$NoRestart"; $script:features[$FeatureName] = 'EnablePending' }}
    function Disable-WindowsOptionalFeature {{ param([switch]$Online, $FeatureName, [switch]$NoRestart)
        Note 'disable' "$FeatureName|$NoRestart" }}
    function Invoke-GcNative {{ param($FilePath, $ArgumentList)
        if ($ArgumentList[0] -eq '/enum') {{ return $script:bcd }}
        Note 'bcdedit' ($ArgumentList -join ' ')
        if ($ArgumentList[0] -eq '/set') {{ $script:bcd += 'bootuxdisabled          Yes' }} }}
"""


def _install(tmp_path, prelude: str, **settings) -> dict:
    return run_ps(tmp_path, prelude + f"Install-GcDeviceLockdown {settings_ps(tmp_path, **settings)}")


def test_enterprise_turns_both_features_on_without_restarting_and_records_them(tmp_path):
    result = _install(tmp_path, fakes())
    assert calls(result, "enable") == [f"{name}|True" for name in FEATURES]
    record = result["manifest"]["enable-device-lockdown"]
    assert {k: v for k, v in record.items() if k.startswith("feature:")} == {
        f"feature:{name}": "Disabled" for name in FEATURES}


def test_custom_logon_hides_the_welcome_screen_and_the_animations(tmp_path):
    result = _install(tmp_path, fakes())
    assert calls(result, "set") == CUSTOM_LOGON_SETS
    record = result["manifest"]["enable-device-lockdown"]
    assert record[rf"{EMBEDDED}\HideAutoLogonUI"] is None and record[rf"{LOGONUI}\AnimationDisabled"] is None


def test_unbranded_boot_turns_the_boot_ui_off_for_every_entry(tmp_path):
    result = _install(tmp_path, fakes())
    assert calls(result, "bcdedit") == ["/set {globalsettings} bootuxdisabled on"]
    assert result["manifest"]["enable-device-lockdown"]["globalsettings:bootuxdisabled"] is None


def test_the_summary_is_told_a_restart_is_needed(tmp_path):
    result = run_ps(tmp_path, fakes() + f"""
        Install-GcDeviceLockdown {settings_ps(tmp_path)}
        $GcTest['restart'] = @($script:GcRestartReasons)
    """)
    assert result["restart"] == [f"Windows feature {name}" for name in FEATURES]


def test_home_says_it_is_not_available_and_changes_nothing(tmp_path):
    result = run_ps(tmp_path, fakes("Core") + rf"""
        function Write-GcInfo {{ param($Message) Note 'info' $Message }}
        function Get-WindowsOptionalFeature {{ throw 'Home must not query features' }}
        Install-GcDeviceLockdown {settings_ps(tmp_path)}
    """)
    assert calls(result, "info") == ["Custom Logon and Unbranded Boot: not available on Core"]
    assert result["changes"] == [] and "enable-device-lockdown" not in result["manifest"]


def test_pro_is_not_available_either(tmp_path):
    result = _install(tmp_path, fakes("Professional"))
    assert result["calls"] == [] and result["changes"] == []


def test_an_enterprise_image_without_the_features_changes_nothing(tmp_path):
    result = _install(tmp_path, fakes("EnterpriseS", features={}))
    assert result["calls"] == [] and result["changes"] == []


def test_ltsc_and_iot_editions_qualify(tmp_path):
    result = run_ps(tmp_path, "$GcTest['editions'] = @('EnterpriseS', 'IoTEnterpriseS', 'EducationN', "
                              "'Core', 'Professional', 'ServerRdsh') | ForEach-Object { Test-GcLockdownEdition $_ }")
    assert result["editions"] == [True, True, True, False, False, False]


def test_features_already_on_are_not_recorded_so_uninstall_leaves_them(tmp_path):
    result = _install(tmp_path, fakes(features=dict.fromkeys(FEATURES, "Enabled")))
    assert calls(result, "enable") == []
    assert not any(k.startswith("feature:") for k in result["manifest"]["enable-device-lockdown"])


def test_only_the_half_this_windows_has_is_configured(tmp_path):
    result = _install(tmp_path, fakes(features={"Client-DeviceLockdown": "Enabled",
                                                "Client-EmbeddedBootExp": "Disabled"}))
    assert calls(result, "enable") == ["Client-EmbeddedBootExp|True"]
    assert calls(result, "set") == [] and calls(result, "bcdedit") == ["/set {globalsettings} bootuxdisabled on"]


def test_a_second_run_changes_nothing(tmp_path):
    result = run_ps(tmp_path, fakes() + f"""
        Install-GcDeviceLockdown {settings_ps(tmp_path)}
        $GcTest.calls.Clear(); $script:GcChanges.Clear()
        Install-GcDeviceLockdown {settings_ps(tmp_path)}
    """)
    assert result["calls"] == [] and result["changes"] == []


def test_quiet_boot_off_skips_the_step(tmp_path):
    result = _install(tmp_path, fakes(), QuietBoot=False)
    assert result["calls"] == [] and result["changes"] == []


def test_a_dry_run_lists_the_plan_and_changes_nothing(tmp_path):
    result = run_ps(tmp_path, fakes() + f"Install-GcDeviceLockdown {settings_ps(tmp_path)}", dry_run=True)
    assert result["calls"] == []
    assert any("Client-EmbeddedLogon" in c for c in result["changes"])
    assert any("{globalsettings} bootuxdisabled on" in c for c in result["changes"])
    assert "set BrandingNeutral = 33" in result["changes"]


def test_an_unelevated_dry_run_on_enterprise_still_plans_the_features(tmp_path):
    result = run_ps(tmp_path, fakes() + f"""
        function Get-WindowsOptionalFeature {{ throw 'The requested operation requires elevation.' }}
        function Test-GcElevated {{ $false }}
        Install-GcDeviceLockdown {settings_ps(tmp_path)}
    """, dry_run=True)
    assert sum("turn on the Windows feature" in c for c in result["changes"]) == 3


def test_uninstall_turns_off_only_its_features_children_first_and_restores_the_rest(tmp_path):
    result = run_ps(tmp_path, fakes() + rf"""
        Uninstall-GcDeviceLockdown @{{
            'feature:Client-EmbeddedLogon' = 'Disabled'; 'feature:Client-EmbeddedBootExp' = 'Disabled'
            'feature:Client-DeviceLockdown' = 'Disabled'
            '{EMBEDDED}\HideAutoLogonUI' = $null; '{EMBEDDED}\BrandingNeutral' = 1
            'globalsettings:bootuxdisabled' = $null }}
    """)
    assert calls(result, "disable") == ["Client-EmbeddedBootExp|True", "Client-EmbeddedLogon|True",
                                        "Client-DeviceLockdown|True"]
    assert calls(result, "restore") == ["HideAutoLogonUI=", "BrandingNeutral=1"]
    assert calls(result, "bcdedit") == ["/deletevalue {globalsettings} bootuxdisabled"]


def test_uninstall_leaves_a_parent_feature_that_was_already_on(tmp_path):
    result = run_ps(tmp_path, fakes() + "Uninstall-GcDeviceLockdown @{ 'feature:Client-EmbeddedLogon' = 'Disabled' }")
    assert calls(result, "disable") == ["Client-EmbeddedLogon|True"]


def test_uninstall_puts_a_previous_off_back_as_off(tmp_path):
    result = run_ps(tmp_path, fakes() + "Uninstall-GcDeviceLockdown @{ 'globalsettings:bootuxdisabled' = 'No' }")
    assert calls(result, "bcdedit") == ["/set {globalsettings} bootuxdisabled off"]
