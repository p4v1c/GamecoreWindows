# GameCore as the console's Windows shell, in place of Explorer (dev log D59):
# HKLM Winlogon `Shell` = install\bin\gamecore-shell, started unelevated by
# Winlogon; it starts the elevated \GameCore\Session task, so the console user
# may run that task. The per-user values do nothing on Windows 11 Home (HKCU
# Winlogon\Shell is mapped to HKLM only by IniFileMapping, the HKCU "Custom
# User Interface" policy is ignored), both measured; leftovers of them are
# removed. HKLM\SOFTWARE\GameCore\KioskShell records the command for
# gamecore-session, which re-arms the shell at every start and points it at
# Explorer for Desktop mode. Skipped with KIOSK=0.

$script:GcMachineWinlogonKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$script:GcKioskMarkerKey = 'HKLM:\SOFTWARE\GameCore'
$script:GcUserShellKeys = @('Software\Microsoft\Windows NT\CurrentVersion\Winlogon',
    'Software\Microsoft\Windows\CurrentVersion\Policies\System')
$script:GcLoadedHive = 'HKEY_USERS\GameCoreConsoleUser'

function Get-GcShellCommand {
    param([Parameter(Mandatory)][string]$CodeRoot)
    $python = Join-Path $CodeRoot '.venv\Scripts\pythonw.exe'
    $shell = Join-Path $CodeRoot 'install\bin\gamecore-shell'
    return "`"$python`" `"$shell`""
}

function Get-GcUserSid {
    param([Parameter(Mandatory)][string]$User)
    $account = New-Object Security.Principal.NTAccount $User
    return $account.Translate([Security.Principal.SecurityIdentifier]).Value
}

function Invoke-GcInUserHive {
    <#
    Run $GcHiveAction with the registry root of $Sid's profile as its argument.
    The hive is loaded for the call when its user is not signed in.
    #>
    param([Parameter(Mandatory)][string]$Sid, [Parameter(Mandatory)][scriptblock]$GcHiveAction)
    $root = "Registry::HKEY_USERS\$Sid"
    if (Test-Path -LiteralPath $root) { return & $GcHiveAction $root }
    $profilePath = Get-GcRegistryValue "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$Sid" 'ProfileImagePath'
    if (-not $profilePath) { throw "no Windows profile for $Sid yet: sign in once as the console user, then re-run" }
    Invoke-GcNative 'reg.exe' @('load', $script:GcLoadedHive, (Join-Path $profilePath 'NTUSER.DAT')) | Out-Null
    try {
        return & $GcHiveAction "Registry::$script:GcLoadedHive"
    } finally {
        [GC]::Collect()   # registry handles PowerShell still holds keep the hive loaded
        Invoke-GcNative 'reg.exe' @('unload', $script:GcLoadedHive) | Out-Null
    }
}

function Add-GcSddlAce {
    <# The task's DACL with read and run for $Sid added, or unchanged when $Sid already has an entry. #>
    param([Parameter(Mandatory)][string]$Sddl, [Parameter(Mandatory)][string]$Sid)
    if ($Sddl -match ";$([regex]::Escape($Sid))\)") { return $Sddl }
    return "$Sddl(A;;FRFX;;;$Sid)"
}

function Get-GcTaskSddl {
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    return $service.GetFolder($script:GcTaskPath.TrimEnd('\')).GetTask($script:GcTaskName).GetSecurityDescriptor(4)
}

function Set-GcTaskSddl {
    param([Parameter(Mandatory)][string]$Sddl)
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    $service.GetFolder($script:GcTaskPath.TrimEnd('\')).GetTask($script:GcTaskName).SetSecurityDescriptor($Sddl, 0)
}

function Grant-GcTaskRun {
    <# The shell runs unelevated and starts the session task on demand (schtasks /run). #>
    param([Parameter(Mandatory)][string]$Sid, [Parameter(Mandatory)][string]$User)
    $description = "let $User start the $script:GcTaskPath$script:GcTaskName task"
    try {
        $sddl = Get-GcTaskSddl
    } catch {
        # A dry run of a first install: the task step has not registered it.
        if ($script:GcDryRun) { Invoke-GcChange $description { }; return }
        throw
    }
    $granted = Add-GcSddlAce -Sddl $sddl -Sid $Sid
    if ($granted -ne $sddl) { Invoke-GcChange $description { Set-GcTaskSddl $granted } }
}

function Remove-GcUserShellLeftover {
    <# The per-user Shell values an earlier GameCore wrote; Windows ignores them, and only GameCore's go. #>
    param([Parameter(Mandatory)][string]$Sid)
    Invoke-GcInUserHive -Sid $Sid -GcHiveAction {
        param($root)
        foreach ($key in ($script:GcUserShellKeys | ForEach-Object { Join-Path $root $_ })) {
            if ("$(Get-GcRegistryValue $key 'Shell')" -notlike '*gamecore-shell*') { continue }
            Invoke-GcChange "remove the per-user Shell value under $key (Windows does not read it)" {
                Restore-GcRegistryValue $key 'Shell' $null 'String'
            }
        }
    }
}

function Install-GcKioskShell {
    param([Parameter(Mandatory)]$Settings)
    if (-not $Settings.Kiosk) { Write-GcInfo 'KIOSK=0: Explorer stays the shell'; return }
    $user = $Settings.ConsoleUser
    $wanted = Get-GcShellCommand $Settings.CodeRoot
    try {
        $sid = Get-GcUserSid $user
    } catch {
        # A dry run may name an account that does not exist yet.
        if (-not $script:GcDryRun) { throw }
        $sid = $null
    }
    if ($sid) {
        Grant-GcTaskRun -Sid $sid -User $user
        Remove-GcUserShellLeftover -Sid $sid
    }
    $current = Get-GcRegistryValue $script:GcMachineWinlogonKey 'Shell'
    if ($current -ne $wanted) {
        Set-GcRecord 'set-kiosk-shell' 'MachineShell' $current
        Invoke-GcChange 'make GameCore the Windows shell (HKLM Winlogon Shell; Explorer only in Desktop mode)' {
            Set-GcRegistryValue $script:GcMachineWinlogonKey 'Shell' $wanted 'String'
        }
    }
    if ((Get-GcRegistryValue $script:GcKioskMarkerKey 'KioskShell') -ne $wanted) {
        Set-GcRecord 'set-kiosk-shell' 'KioskShell' (Get-GcRegistryValue $script:GcKioskMarkerKey 'KioskShell')
        Invoke-GcChange 'record the GameCore shell for gamecore-session (re-armed at each start)' {
            Set-GcRegistryValue $script:GcKioskMarkerKey 'KioskShell' $wanted 'String'
        }
    }
}

function Uninstall-GcKioskShell {
    param($Record)
    if ($null -eq $Record) { return }
    $previous = if ($Record.ContainsKey('MachineShell')) { "$($Record['MachineShell'])" } else { '' }
    # Never leave Windows without a shell, nor with GameCore's after its code is gone.
    if (-not $previous -or $previous -like '*gamecore-shell*') { $previous = 'explorer.exe' }
    if ("$(Get-GcRegistryValue $script:GcMachineWinlogonKey 'Shell')" -like '*gamecore-shell*') {
        Invoke-GcChange "give Windows its shell back ($previous)" {
            Set-GcRegistryValue $script:GcMachineWinlogonKey 'Shell' $previous 'String'
        }
    }
    if ($null -ne (Get-GcRegistryValue $script:GcKioskMarkerKey 'KioskShell')) {
        Invoke-GcChange 'remove the GameCore shell record' {
            Restore-GcRegistryValue $script:GcKioskMarkerKey 'KioskShell' $null 'String'
        }
    }
    if ($Record.ContainsKey('sid')) { Remove-GcUserShellLeftover -Sid $Record['sid'] }
    Write-GcInfo 'sign out and back in (Ctrl+Alt+Del, Sign out) to get the Windows desktop'
}
