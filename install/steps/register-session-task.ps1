# The \GameCore\Session task: at the console user's sign-in, run
# install\bin\gamecore-session elevated in that session (dev log D8). The
# backend and the interface both run from it, as administrator (owner, 20/10):
# games inherit the token, and the device wake settings need it (D36).

$script:GcTaskPath = '\GameCore\'
$script:GcTaskName = 'Session'

function Get-GcSessionAction {
    param([Parameter(Mandatory)][string]$CodeRoot)
    return [pscustomobject]@{
        Execute   = Join-Path $CodeRoot '.venv\Scripts\pythonw.exe'
        Arguments = "`"$(Join-Path $CodeRoot 'install\bin\gamecore-session')`""
        WorkingDirectory = $CodeRoot
    }
}

function Test-GcSessionTask {
    <# True when the registered task already runs this action for this user, elevated. #>
    param([Parameter(Mandatory)]$Settings)
    $task = Get-ScheduledTask -TaskPath $script:GcTaskPath -TaskName $script:GcTaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { return $false }
    $want = Get-GcSessionAction $Settings.CodeRoot
    $action = @($task.Actions)[0]
    return ($action.Execute -ieq $want.Execute) -and ($action.Arguments -eq $want.Arguments) -and
        ("$($task.Principal.RunLevel)" -eq 'Highest') -and
        ($task.Principal.UserId.Split('\')[-1] -ieq $Settings.ConsoleUser.Split('\')[-1])
}

function Register-GcSessionTask {
    param([Parameter(Mandatory)]$Settings)
    $want = Get-GcSessionAction $Settings.CodeRoot
    $user = $Settings.ConsoleUser
    if (-not $user.Contains('\')) { $user = "$env:COMPUTERNAME\$user" }
    $action = New-ScheduledTaskAction -Execute $want.Execute -Argument $want.Arguments -WorkingDirectory $want.WorkingDirectory
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    # A console runs for days, on battery for a mini PC on a UPS too; one copy per session.
    $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskPath $script:GcTaskPath -TaskName $script:GcTaskName -Action $action `
        -Trigger $trigger -Principal $principal -Settings $taskSettings `
        -Description 'GameCore: the backend and the interface, in the console session' -Force | Out-Null
}

function Install-GcSessionTask {
    param([Parameter(Mandatory)]$Settings)
    if (Test-GcSessionTask $Settings) { return }
    Set-GcRecord 'register-session-task' 'task' "$script:GcTaskPath$script:GcTaskName"
    Invoke-GcChange "register the $script:GcTaskPath$script:GcTaskName logon task for $($Settings.ConsoleUser), elevated" {
        Register-GcSessionTask $Settings
    }
}

function Stop-GcSessionProcesses {
    <# The session, the backend and the shell: every process whose executable is in the code root. #>
    param([Parameter(Mandatory)][string]$CodeRoot)
    $root = [IO.Path]::GetFullPath($CodeRoot).TrimEnd('\') + '\'
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

function Uninstall-GcSessionTask {
    param($Record, [Parameter(Mandatory)]$Settings)
    $task = Get-ScheduledTask -TaskPath $script:GcTaskPath -TaskName $script:GcTaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { return }
    Invoke-GcChange "remove the $script:GcTaskPath$script:GcTaskName task" {
        Stop-ScheduledTask -TaskPath $script:GcTaskPath -TaskName $script:GcTaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskPath $script:GcTaskPath -TaskName $script:GcTaskName -Confirm:$false
        Stop-GcSessionProcesses $Settings.CodeRoot
    }
}
