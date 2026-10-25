# GameCore as the console user's Windows shell, in place of Explorer (dev log
# D59): the per-user Winlogon `Shell` value, which every edition honours (Shell
# Launcher is Enterprise and Education only). Winlogon starts
# install\bin\gamecore-shell unelevated; it starts the elevated \GameCore\Session
# task, so the console user may run that task. Explorer comes only with Desktop
# mode, as Plasma does on the Linux box. Skipped with KIOSK=0.

$script:GcUserWinlogonKey = 'Software\Microsoft\Windows NT\CurrentVersion\Winlogon'
$script:GcUserGameCoreKey = 'Software\GameCore'
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

function Install-GcKioskShell {
    param([Parameter(Mandatory)]$Settings)
    if (-not $Settings.Kiosk) { Write-GcInfo 'KIOSK=0: Explorer stays the shell'; return }
    $user = $Settings.ConsoleUser
    $description = "make GameCore the Windows shell of $user (Explorer only in Desktop mode)"
    try {
        $sid = Get-GcUserSid $user
    } catch {
        # A dry run may name an account that does not exist yet.
        if ($script:GcDryRun) { Invoke-GcChange $description { }; return }
        throw
    }
    Grant-GcTaskRun -Sid $sid -User $user
    $wanted = Get-GcShellCommand $Settings.CodeRoot
    Invoke-GcInUserHive -Sid $sid -GcHiveAction {
        param($root)
        $key = Join-Path $root $script:GcUserWinlogonKey
        $current = Get-GcRegistryValue $key 'Shell'
        if ($current -eq $wanted) { Write-GcInfo "GameCore is already the shell of $user"; return }
        Set-GcRecord 'set-kiosk-shell' 'sid' $sid
        Set-GcRecord 'set-kiosk-shell' 'Shell' $current
        Invoke-GcChange $description {
            Set-GcRegistryValue $key 'Shell' $wanted 'String'
        }
    }
}

function Uninstall-GcKioskShell {
    param($Record)
    if ($null -eq $Record -or -not $Record.ContainsKey('sid')) { return }
    $previous = $Record['Shell']
    Invoke-GcInUserHive -Sid $Record['sid'] -GcHiveAction {
        param($root)
        $winlogon = Join-Path $root $script:GcUserWinlogonKey
        $gamecore = Join-Path $root $script:GcUserGameCoreKey
        Invoke-GcChange 'give the console user Explorer back as the shell' {
            Restore-GcRegistryValue $winlogon 'Shell' $previous 'String'
            Restore-GcRegistryValue $gamecore 'ShellRearm' $null 'String'
        }
    }
    Write-GcInfo 'sign out and back in (Ctrl+Alt+Del, Sign out) to get the Windows desktop'
}
