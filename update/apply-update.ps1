# The OTA updater, stage 2: stop GameCore, swap the code root for the release
# update/windows.py unpacked beside it, restart, and put the previous version
# back by itself if the new one does not answer.
#
# Started detached by stage 1 from the work directory (a copy, never from the
# code root: that folder is renamed). Elevated, inherited from the backend.
#
#   apply-update.ps1 -Plan <work>\plan.json            what stage 1 runs
#   apply-update.ps1 -Rollback [-CodeRoot <path>] [-EnvFile <session.env>]
#                                                      go back to <code>.prev by hand
#
# ASCII only: Windows PowerShell 5.1 reads a script without a BOM as ANSI.

[CmdletBinding()]
param(
    [string]$Plan,
    [switch]$Rollback,
    [string]$CodeRoot = (Join-Path $env:ProgramFiles 'GameCore'),
    [string]$EnvFile = (Join-Path $env:ProgramData 'GameCore\session.env')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:GcUpdateLogFile = $null
# Seconds. The new backend imports, opens the database and binds in about 10 s;
# 3 minutes covers a slow disk and a first start that rebuilds caches.
$script:GcUpdateReadyTimeoutS = 180
$script:GcUpdateStopTimeoutS = 30
$script:GcUpdateMoveAttempts = 10
# Lets stage 1's last line and update:done reach the interface before it is stopped.
$script:GcUpdateHandOverDelayS = 3

function Write-GcUpdateLog {
    param([Parameter(Mandatory)][string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    if ($script:GcUpdateLogFile) {
        try { [IO.File]::AppendAllText($script:GcUpdateLogFile, "$line`r`n") } catch { $null = $_ }
    }
    # The host, not the pipeline: a log line must never become part of a return value.
    Write-Host $line
}

function Initialize-GcUpdateLog {
    param([Parameter(Mandatory)][string]$Path)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $script:GcUpdateLogFile = $Path
}

function Get-GcUpdateSessionProcess {
    <# Every process whose executable lives in the code root: the session, the backend, the shell. #>
    param([Parameter(Mandatory)][string]$Root)
    $prefix = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    return @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) })
}

function Stop-GcUpdateSession {
    <# Stop GameCore and Caddy, whose files hold the code root. Returns whether Caddy was running. #>
    param([Parameter(Mandatory)]$UpdatePlan)
    # The supervisor first: it restarts a killed backend within 5 s. Stop-Process
    # -Force is TerminateProcess, like taskkill /F: a WM_CLOSE makes the kiosk
    # shell rebuild its window instead of leaving.
    $all = @(Get-GcUpdateSessionProcess $UpdatePlan.code_root)
    $first = @($all | Where-Object { $_.Name -ieq 'pythonw' })
    foreach ($group in @($first), @($all | Where-Object { $_.Name -ine 'pythonw' })) {
        foreach ($process in $group) {
            Write-GcUpdateLog "stopping $($process.Name) ($($process.Id))"
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    $deadline = (Get-Date).AddSeconds($script:GcUpdateStopTimeoutS)
    while (@(Get-GcUpdateSessionProcess $UpdatePlan.code_root).Count -gt 0) {
        if ((Get-Date) -gt $deadline) { throw "GameCore processes still running after $($script:GcUpdateStopTimeoutS) s" }
        Start-Sleep -Milliseconds 500
    }
    $caddy = Get-Service -Name $UpdatePlan.caddy_service -ErrorAction SilentlyContinue
    if ($null -ne $caddy -and "$($caddy.Status)" -eq 'Running') {
        Write-GcUpdateLog "stopping the $($UpdatePlan.caddy_service) service"
        Stop-Service -Name $UpdatePlan.caddy_service -Force
        return $true
    }
    return $false
}

function Move-GcUpdateDirectory {
    <# Rename with retries: an antivirus or the indexer can hold a file for a moment. #>
    param([Parameter(Mandatory)][string]$From, [Parameter(Mandatory)][string]$To)
    for ($attempt = 1; ; $attempt++) {
        try {
            Move-Item -LiteralPath $From -Destination $To
            return
        } catch {
            if ($attempt -ge $script:GcUpdateMoveAttempts) { throw "cannot move $From to ${To}: $($_.Exception.Message)" }
            Start-Sleep -Seconds 1
        }
    }
}

function Remove-GcUpdateDirectory {
    <# Clear a leftover; if it cannot go, rename it aside so the swap still has its name. #>
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force
    } catch {
        $aside = "$Path-$(Get-Date -Format 'yyyyMMddHHmmss')"
        Write-GcUpdateLog "WARNING: cannot remove $Path, moved to $aside"
        Move-GcUpdateDirectory $Path $aside
    }
}

function Install-GcUpdateTree {
    <# code -> previous, staging -> code, then the installed pieces (Python, venv, Caddy) follow. #>
    param([Parameter(Mandatory)]$UpdatePlan)
    $code = $UpdatePlan.code_root
    $previous = $UpdatePlan.previous
    Remove-GcUpdateDirectory $previous
    Move-GcUpdateDirectory $code $previous
    try {
        Move-GcUpdateDirectory $UpdatePlan.staging $code
    } catch {
        Move-GcUpdateDirectory $previous $code
        throw
    }
    foreach ($name in @($UpdatePlan.carry)) {
        $from = Join-Path $previous $name
        $to = Join-Path $code $name
        if ((Test-Path -LiteralPath $from) -and -not (Test-Path -LiteralPath $to)) {
            Move-GcUpdateDirectory $from $to
        }
    }
    Write-GcUpdateLog "code root swapped; the previous version is in $previous"
}

function Undo-GcUpdateTree {
    <# The reverse: the installed pieces go back, code -> failed, previous -> code. #>
    param([Parameter(Mandatory)]$UpdatePlan)
    $code = $UpdatePlan.code_root
    $previous = $UpdatePlan.previous
    if (-not (Test-Path -LiteralPath $previous)) { throw "no previous version at $previous" }
    foreach ($name in @($UpdatePlan.carry)) {
        $from = Join-Path $code $name
        $to = Join-Path $previous $name
        if ((Test-Path -LiteralPath $from) -and -not (Test-Path -LiteralPath $to)) {
            Move-GcUpdateDirectory $from $to
        }
    }
    Remove-GcUpdateDirectory $UpdatePlan.failed
    if (Test-Path -LiteralPath $code) { Move-GcUpdateDirectory $code $UpdatePlan.failed }
    Move-GcUpdateDirectory $previous $code
    Write-GcUpdateLog "previous version restored; the one that failed is in $($UpdatePlan.failed)"
}

function Invoke-GcUpdateNative {
    <# Run a program, log its output, throw on a non-zero exit code. #>
    param([Parameter(Mandatory)][string]$FilePath, [string[]]$ArgumentList = @())
    # Each stderr line is an error record in Windows PowerShell; under 'Stop' the
    # first pip warning would throw and roll a good update back.
    $ErrorActionPreference = 'Continue'
    $output = @(& $FilePath @ArgumentList 2>&1 | ForEach-Object { "$_" })
    $ErrorActionPreference = 'Stop'
    foreach ($line in $output) { Write-GcUpdateLog "  $line" }
    if ($LASTEXITCODE -ne 0) { throw "$(Split-Path -Leaf $FilePath) exited with $LASTEXITCODE" }
}

function Install-GcUpdateRequirement {
    <# pip only when backend\requirements.txt changed; same stamp as install\steps\install-python.ps1. #>
    param([Parameter(Mandatory)][string]$Root)
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    $requirements = Join-Path $Root 'backend\requirements.txt'
    $stamp = Join-Path $Root '.venv\gamecore-requirements.sha256'
    if (-not (Test-Path -LiteralPath $python)) {
        Write-GcUpdateLog "WARNING: no venv at $python; Python packages not checked"
        return
    }
    $wanted = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $installed = if (Test-Path -LiteralPath $stamp) { [IO.File]::ReadAllText($stamp).Trim() } else { '' }
    if ($installed -eq $wanted) { return }
    Write-GcUpdateLog 'installing the backend Python packages'
    Invoke-GcUpdateNative $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-q', '-r', $requirements)
    [IO.File]::WriteAllText($stamp, $wanted)
}

function Invoke-GcUpdateFinish {
    <# Themes and the catalogue merge, in the data root. Never fatal: the code is in place. #>
    param([Parameter(Mandatory)]$UpdatePlan)
    $python = Join-Path $UpdatePlan.code_root '.venv\Scripts\python.exe'
    $script = Join-Path $UpdatePlan.code_root 'update\windows.py'
    if (-not (Test-Path -LiteralPath $python)) { return }
    $env:PYTHONUTF8 = '1'
    try {
        Invoke-GcUpdateNative $python @($script, 'finish', '--root', $UpdatePlan.code_root, '--data', $UpdatePlan.data_root)
    } catch {
        Write-GcUpdateLog "WARNING: themes and catalogue not refreshed: $($_.Exception.Message)"
    }
}

function Start-GcUpdateSession {
    <# Caddy back if it ran, then the logon task (elevated, console session); the session itself without one. #>
    param([Parameter(Mandatory)]$UpdatePlan, [bool]$CaddyWasRunning)
    if ($CaddyWasRunning) {
        try { Start-Service -Name $UpdatePlan.caddy_service } catch { Write-GcUpdateLog "WARNING: $($_.Exception.Message)" }
    }
    $task = Get-ScheduledTask -TaskPath $UpdatePlan.task_path -TaskName $UpdatePlan.task_name -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Write-GcUpdateLog "starting $($UpdatePlan.task_path)$($UpdatePlan.task_name)"
        Start-ScheduledTask -TaskPath $UpdatePlan.task_path -TaskName $UpdatePlan.task_name
        return
    }
    $pythonw = Join-Path $UpdatePlan.code_root '.venv\Scripts\pythonw.exe'
    $session = Join-Path $UpdatePlan.code_root 'install\bin\gamecore-session'
    if (Test-Path -LiteralPath $pythonw) {
        Write-GcUpdateLog 'no logon task; starting gamecore-session directly'
        Start-Process -FilePath $pythonw -ArgumentList "`"$session`"" -WorkingDirectory $UpdatePlan.code_root
    } else {
        Write-GcUpdateLog 'WARNING: no logon task and no venv: nothing to start'
    }
}

function Test-GcUpdateAlive {
    <# The backend answers /api/ready at all: 503 (still starting) counts, a refused connection does not. #>
    param([Parameter(Mandatory)][string]$Port)
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri "http://127.0.0.1:$Port/api/ready" | Out-Null
        return $true
    } catch [System.Net.WebException] {
        return $null -ne $_.Exception.Response
    }
}

function Wait-GcUpdateReady {
    param([Parameter(Mandatory)][string]$Port, [int]$TimeoutS = $script:GcUpdateReadyTimeoutS)
    $deadline = (Get-Date).AddSeconds($TimeoutS)
    while ((Get-Date) -lt $deadline) {
        if (Test-GcUpdateAlive $Port) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Write-GcUpdateResult {
    param([Parameter(Mandatory)]$UpdatePlan, [Parameter(Mandatory)][string]$Result, [string]$Detail = '')
    $record = [ordered]@{ from = $UpdatePlan.from; to = $UpdatePlan.to; result = $Result; detail = $Detail
        finished = (Get-Date).ToString('o') }
    [IO.File]::WriteAllText($UpdatePlan.result, ($record | ConvertTo-Json))
    Write-GcUpdateLog "result: $Result $Detail"
}

function Invoke-GcUpdate {
    <# The whole of stage 2. Any failure after the swap puts the previous version back. #>
    param([Parameter(Mandatory)]$UpdatePlan)
    Start-Sleep -Seconds $script:GcUpdateHandOverDelayS
    [IO.File]::WriteAllText($UpdatePlan.lock, "$PID")
    Write-GcUpdateLog "installing $($UpdatePlan.to) over $($UpdatePlan.from)"
    $swapped = $false
    $caddy = $false
    try {
        $caddy = Stop-GcUpdateSession $UpdatePlan
        Install-GcUpdateTree $UpdatePlan
        $swapped = $true
        Install-GcUpdateRequirement $UpdatePlan.code_root
        Invoke-GcUpdateFinish $UpdatePlan
        Start-GcUpdateSession $UpdatePlan $caddy
        if (-not (Wait-GcUpdateReady $UpdatePlan.port)) {
            throw "$($UpdatePlan.to) did not answer within $($script:GcUpdateReadyTimeoutS) s"
        }
        Write-GcUpdateResult $UpdatePlan 'installed'
    } catch {
        $reason = $_.Exception.Message
        Write-GcUpdateLog "ERROR: $reason"
        # The staging folder gone means the new code root is in place, maybe
        # without its venv: a failed carry needs the undo as much as a failed start.
        if (-not (Test-Path -LiteralPath $UpdatePlan.staging) -and (Test-Path -LiteralPath $UpdatePlan.previous)) {
            $swapped = $true
        }
        try {
            if ($swapped) {
                Stop-GcUpdateSession $UpdatePlan | Out-Null
                Undo-GcUpdateTree $UpdatePlan
            }
            Start-GcUpdateSession $UpdatePlan $caddy
            $back = Wait-GcUpdateReady $UpdatePlan.port
            $state = if ($swapped) { 'rolled-back' } else { 'failed' }
            Write-GcUpdateResult $UpdatePlan $state "$reason; previous version answering: $back"
        } catch {
            Write-GcUpdateResult $UpdatePlan 'broken' "$reason; then: $($_.Exception.Message)"
        }
    } finally {
        Remove-Item -LiteralPath $UpdatePlan.lock -Force -ErrorAction SilentlyContinue
    }
}

function Get-GcRollbackPlan {
    <# A plan for -Rollback, from the code root and the session's settings file. #>
    param([Parameter(Mandatory)][string]$Root, [string]$EnvFile = (Join-Path $env:ProgramData 'GameCore\session.env'))
    $values = @{}
    if (Test-Path -LiteralPath $EnvFile) {
        foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
            if ($line -match '^\s*([A-Z_]+)\s*=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    $root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $data = if ($values.ContainsKey('GAMECORE_DATA')) { $values['GAMECORE_DATA'] } else { $root }
    $port = if ($values.ContainsKey('GAMECORE_BACKEND_PORT')) { $values['GAMECORE_BACKEND_PORT'] } else { '8765' }
    $state = Split-Path -Parent $EnvFile
    return [pscustomobject]@{
        code_root = $root; previous = "$root.prev"; failed = "$root.failed"; staging = "$root.new"
        data_root = $data; port = $port; from = 'installed'; to = 'previous'
        carry = @('python', '.venv', 'caddy'); task_path = '\GameCore\'; task_name = 'Session'
        caddy_service = 'GameCoreCaddy'; lock = (Join-Path $state 'update\update.lock')
        result = (Join-Path $state 'update\last-update.json')
        log = (Join-Path $data 'logs\update\update.log')
    }
}

function Invoke-GcUpdateRollback {
    <# By hand: the version in <code>.prev becomes the code root again. #>
    param([Parameter(Mandatory)]$UpdatePlan)
    $caddy = Stop-GcUpdateSession $UpdatePlan
    Undo-GcUpdateTree $UpdatePlan
    Start-GcUpdateSession $UpdatePlan $caddy
    $back = Wait-GcUpdateReady $UpdatePlan.port
    Write-GcUpdateResult $UpdatePlan 'rolled-back' "by hand; previous version answering: $back"
}

function Test-GcInsideTree {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Root)
    $prefix = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    return [IO.Path]::GetFullPath($Path).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

# Dot-sourced by the tests: functions only, nothing runs.
if ($MyInvocation.InvocationName -eq '.') { return }

if ($Rollback) {
    $rollbackPlan = Get-GcRollbackPlan $CodeRoot $EnvFile
    # Run from a copy: the folder this file is in is about to be renamed.
    foreach ($tree in $rollbackPlan.code_root, $rollbackPlan.previous) {
        if (Test-GcInsideTree $PSCommandPath $tree) {
            $copy = Join-Path ([IO.Path]::GetTempPath()) 'gamecore-apply-update.ps1'
            Copy-Item -LiteralPath $PSCommandPath -Destination $copy -Force
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $copy -Rollback -CodeRoot $CodeRoot -EnvFile $EnvFile
            exit $LASTEXITCODE
        }
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $rollbackPlan.result) | Out-Null
    Initialize-GcUpdateLog $rollbackPlan.log
    Invoke-GcUpdateRollback $rollbackPlan
    exit 0
}
if (-not $Plan) { throw 'give -Plan <plan.json> (stage 1 does) or -Rollback' }
$updatePlan = [IO.File]::ReadAllText($Plan) | ConvertFrom-Json
Initialize-GcUpdateLog $updatePlan.log
Invoke-GcUpdate $updatePlan
