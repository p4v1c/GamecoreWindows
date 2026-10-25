# Windows' Device Lockdown features, where the edition has them (dev log D66):
# Custom Logon hides the Welcome screen during automatic sign-in, the sign-in
# animations and the sign-in screen's buttons; Unbranded Boot hides the logo,
# spinner and status text for every boot entry. Enterprise, Education and IoT
# Enterprise (LTSC included) only; on Home and Pro it says so and changes
# nothing. Skipped with QUIET_BOOT=0. Settings from learn.microsoft.com
# "Custom Logon" and "Unbranded Boot".

$script:GcLockdownStep = 'enable-device-lockdown'
$script:GcLockdownEditionPattern = '^(Enterprise|Education|IoTEnterprise)'
$script:GcLockdownParent = 'Client-DeviceLockdown'
$script:GcCustomLogonFeature = 'Client-EmbeddedLogon'
$script:GcUnbrandedBootFeature = 'Client-EmbeddedBootExp'
$script:GcFeatureOnStates = @('Enabled', 'EnablePending')
$script:GcEmbeddedLogonKey = 'HKLM:\SOFTWARE\Microsoft\Windows Embedded\EmbeddedLogon'
# 0x1 drops the Power, Language and Ease of Access buttons, 0x20 the "apps are
# preventing shutdown" screen (blocking apps are closed). Switch user (0x10)
# stays: Ctrl+Alt+Del keeps Task Manager, Sign out and Switch user as a way out.
$script:GcBrandingNeutral = 0x21
$script:GcCustomLogonValues = @(
    [pscustomobject]@{ Path = $script:GcEmbeddedLogonKey; Name = 'HideAutoLogonUI'; Value = 1 }
    [pscustomobject]@{ Path = $script:GcEmbeddedLogonKey; Name = 'HideFirstLogonAnimation'; Value = 1 }
    [pscustomobject]@{ Path = $script:GcEmbeddedLogonKey; Name = 'BrandingNeutral'; Value = $script:GcBrandingNeutral }
    [pscustomobject]@{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI'; Name = 'AnimationDisabled'; Value = 1 }
)
$script:GcGlobalBootUxKey = 'globalsettings:bootuxdisabled'

function Test-GcLockdownEdition {
    param([AllowNull()][string]$Edition)
    return [bool]($Edition -and $Edition -match $script:GcLockdownEditionPattern)
}

function Get-GcFeatureState {
    <# A Windows optional feature's state, $null when this Windows lacks it, 'Unknown' in an unelevated dry run. #>
    param([Parameter(Mandatory)][string]$Name)
    try {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $Name -ErrorAction Stop
    } catch {
        # The feature query needs elevation; a dry run does not.
        if ($script:GcDryRun -and -not (Test-GcElevated)) { return 'Unknown' }
        return $null
    }
    if ($null -eq $feature) { return $null }
    return "$($feature.State)"
}

function Get-GcLockdownPlan {
    <# Which features this PC has: @{ Parent; CustomLogon; UnbrandedBoot } states, or $null when none. #>
    $plan = [pscustomobject]@{
        Parent        = Get-GcFeatureState $script:GcLockdownParent
        CustomLogon   = Get-GcFeatureState $script:GcCustomLogonFeature
        UnbrandedBoot = Get-GcFeatureState $script:GcUnbrandedBootFeature
    }
    if (-not $plan.CustomLogon -and -not $plan.UnbrandedBoot) { return $null }
    return $plan
}

function Enable-GcLockdownFeature {
    <# Turn a feature on unless it is; the state it had is recorded, so uninstall turns off only ours. #>
    param([Parameter(Mandatory)][string]$Name, [AllowNull()][string]$State)
    if (-not $State -or $script:GcFeatureOnStates -contains $State) { return }
    Set-GcRecord $script:GcLockdownStep "feature:$Name" $State
    Add-GcRestartReason "Windows feature $Name"
    Invoke-GcChange "turn on the Windows feature $Name (applies at the next restart)" {
        Enable-WindowsOptionalFeature -Online -FeatureName $Name -NoRestart -ErrorAction Stop | Out-Null
    }
}

function Install-GcCustomLogon {
    foreach ($setting in $script:GcCustomLogonValues) {
        $current = Get-GcRegistryValue $setting.Path $setting.Name
        if ($null -ne $current -and [int]$current -eq $setting.Value) { continue }
        Set-GcRecord $script:GcLockdownStep "$($setting.Path)\$($setting.Name)" $current
        Invoke-GcChange "set $($setting.Name) = $($setting.Value)" {
            Set-GcRegistryValue $setting.Path $setting.Name $setting.Value
        }
    }
}

function Install-GcUnbrandedBoot {
    try {
        $bootUx = Get-GcBootUxDisabled '{globalsettings}'
    } catch {
        if (-not $script:GcDryRun) { throw }
        $bootUx = $null
    }
    if (Test-GcBcdOn $bootUx) { return }
    Set-GcRecord $script:GcLockdownStep $script:GcGlobalBootUxKey $bootUx
    Invoke-GcChange 'hide the Windows logo, spinner and status text for every boot entry (bcdedit {globalsettings} bootuxdisabled on)' {
        Invoke-GcNative 'bcdedit.exe' @('/set', '{globalsettings}', 'bootuxdisabled', 'on') | Out-Null
    }
}

function Install-GcDeviceLockdown {
    param([Parameter(Mandatory)]$Settings)
    if (-not $Settings.QuietBoot) { Write-GcInfo 'QUIET_BOOT=0: skipped'; return }
    $edition = Get-GcEditionId
    $name = if ($edition) { $edition } else { 'this edition' }
    if (-not (Test-GcLockdownEdition $edition)) { Write-GcInfo "Custom Logon and Unbranded Boot: not available on $name"; return }
    $plan = Get-GcLockdownPlan
    if ($null -eq $plan) { Write-GcInfo "Custom Logon and Unbranded Boot: not available on $name (features absent)"; return }
    Enable-GcLockdownFeature $script:GcLockdownParent $plan.Parent
    if ($plan.CustomLogon) {
        Enable-GcLockdownFeature $script:GcCustomLogonFeature $plan.CustomLogon
        Install-GcCustomLogon
    }
    if ($plan.UnbrandedBoot) {
        Enable-GcLockdownFeature $script:GcUnbrandedBootFeature $plan.UnbrandedBoot
        Install-GcUnbrandedBoot
    }
}

function Uninstall-GcDeviceLockdown {
    param($Record)
    if ($null -eq $Record) { return }
    if ($Record.ContainsKey($script:GcGlobalBootUxKey)) {
        $previous = $Record[$script:GcGlobalBootUxKey]
        $arguments = if ($null -eq $previous) { @('/deletevalue', '{globalsettings}', 'bootuxdisabled') }
        else { @('/set', '{globalsettings}', 'bootuxdisabled', $(if (Test-GcBcdOn $previous) { 'on' } else { 'off' })) }
        Invoke-GcChange 'put the boot logo setting for every entry back' { Invoke-GcNative 'bcdedit.exe' $arguments | Out-Null }
    }
    foreach ($setting in $script:GcCustomLogonValues) {
        $key = "$($setting.Path)\$($setting.Name)"
        if (-not $Record.ContainsKey($key)) { continue }
        $previous = $Record[$key]
        Invoke-GcChange "put $($setting.Name) back" { Restore-GcRegistryValue $setting.Path $setting.Name $previous }
    }
    # Children before their parent; only the features this step turned on.
    $ours = @($script:GcUnbrandedBootFeature, $script:GcCustomLogonFeature, $script:GcLockdownParent) |
        Where-Object { $Record.ContainsKey("feature:$_") }
    foreach ($feature in $ours) {
        Invoke-GcChange "turn off the Windows feature $feature (applies at the next restart)" {
            Disable-WindowsOptionalFeature -Online -FeatureName $feature -NoRestart -ErrorAction Stop | Out-Null
        }
    }
    if ($ours) { Write-GcInfo 'restart the PC to finish removing the Windows features' }
}
