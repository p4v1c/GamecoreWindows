# A boot as quiet as Windows allows, as close to the Linux box's quiet boot as
# it gets (dev log D60): no Windows logo and spinner (bcdedit bootuxdisabled),
# no first sign-in animation, no "Welcome"/"Preparing" status text, a plain
# sign-in background; no lock screen where the edition honours the policy
# (Enterprise, Education). The firmware's own logo is the PC's BIOS setting,
# out of Windows' reach. Skipped with QUIET_BOOT=0.

$script:GcBootPolicies = @(
    [pscustomobject]@{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'; Name = 'EnableFirstLogonAnimation'; Value = 0 }
    [pscustomobject]@{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'; Name = 'DisableStatusMessages'; Value = 1 }
    [pscustomobject]@{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System'; Name = 'DisableLogonBackgroundImage'; Value = 1 }
)
$script:GcLockScreenPolicy = [pscustomobject]@{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization'; Name = 'NoLockScreen'; Value = 1 }
# bcdedit translates Yes/No in its listing; these are the spellings of "on".
$script:GcBcdOnPattern = '^(yes|on|true|1|oui|ja|s.|sim)$'

function Get-GcBootUxDisabled {
    <# The raw bootuxdisabled value of a BCD object (the running entry by default), or $null when it is not set. #>
    param([string]$BcdObject = '{current}')
    foreach ($line in (Invoke-GcNative 'bcdedit.exe' @('/enum', $BcdObject))) {
        if ($line -match '^\s*bootuxdisabled\s+(\S+)') { return $Matches[1] }
    }
    return $null
}

function Test-GcBcdOn {
    param([AllowNull()][string]$Value)
    return [bool]($Value -and $Value.Trim() -match $script:GcBcdOnPattern)
}

function Test-GcLockScreenPolicyHonoured {
    <# Windows ignores NoLockScreen outside Enterprise and Education (since 1607). #>
    return [bool]("$(Get-GcEditionId)" -match '^(Enterprise|Education|IoTEnterprise|ServerRdsh)')
}

function Get-GcBootPolicy {
    if (Test-GcLockScreenPolicyHonoured) { return @($script:GcBootPolicies) + $script:GcLockScreenPolicy }
    Write-GcInfo 'the lock screen stays: this Windows edition ignores the policy (no password on wake is set by configure-power)'
    return @($script:GcBootPolicies)
}

function Install-GcQuietBoot {
    param([Parameter(Mandatory)]$Settings)
    if (-not $Settings.QuietBoot) { Write-GcInfo 'QUIET_BOOT=0: skipped'; return }
    try {
        $bootUx = Get-GcBootUxDisabled
    } catch {
        # bcdedit reads the boot store as administrator only; a dry run needs no elevation.
        if (-not $script:GcDryRun) { throw }
        $bootUx = $null
    }
    if (-not (Test-GcBcdOn $bootUx)) {
        Set-GcRecord 'configure-boot-visuals' 'bootuxdisabled' $bootUx
        Invoke-GcChange 'hide the Windows logo and spinner at boot (bcdedit bootuxdisabled on)' {
            Invoke-GcNative 'bcdedit.exe' @('/set', '{current}', 'bootuxdisabled', 'on') | Out-Null
        }
    }
    foreach ($policy in (Get-GcBootPolicy)) {
        $current = Get-GcRegistryValue $policy.Path $policy.Name
        if ($null -ne $current -and [int]$current -eq $policy.Value) { continue }
        Set-GcRecord 'configure-boot-visuals' "$($policy.Path)\$($policy.Name)" $current
        Invoke-GcChange "set $($policy.Name) = $($policy.Value)" {
            Set-GcRegistryValue $policy.Path $policy.Name $policy.Value
        }
    }
}

function Uninstall-GcQuietBoot {
    param($Record)
    if ($null -eq $Record) { return }
    if ($Record.ContainsKey('bootuxdisabled')) {
        $previous = $Record['bootuxdisabled']
        $arguments = if ($null -eq $previous) { @('/deletevalue', '{current}', 'bootuxdisabled') }
        else { @('/set', '{current}', 'bootuxdisabled', $(if (Test-GcBcdOn $previous) { 'on' } else { 'off' })) }
        Invoke-GcChange 'put the Windows boot logo back as it was' { Invoke-GcNative 'bcdedit.exe' $arguments | Out-Null }
    }
    foreach ($policy in (@($script:GcBootPolicies) + $script:GcLockScreenPolicy)) {
        $key = "$($policy.Path)\$($policy.Name)"
        if (-not $Record.ContainsKey($key)) { continue }
        $previous = $Record[$key]
        Invoke-GcChange "put $($policy.Name) back" { Restore-GcRegistryValue $policy.Path $policy.Name $previous }
    }
}
