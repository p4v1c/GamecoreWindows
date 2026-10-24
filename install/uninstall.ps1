<#
.SYNOPSIS
    Remove GameCore for Windows and put back what install.ps1 changed.

.DESCRIPTION
    Runs the install steps backwards from the record install.ps1 kept in
    %ProgramData%\GameCore\install-manifest.json: the logon task, automatic
    sign-in, the power settings, the firewall rules, OpenSSH (only if GameCore
    installed it), Caddy's service, session.env and the code root. The data
    root (ROMs, saves, covers, config) is never touched. Elevates itself.

.EXAMPLE
    .\install\uninstall.ps1 -DryRun
    Prints what would be removed or restored, and changes nothing.
#>
[CmdletBinding()]
param(
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')
. (Join-Path $PSScriptRoot 'lib\steps.ps1')

$script:GcDryRun = [bool]$DryRun
if (-not $DryRun -and -not (Test-GcElevated)) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

# Out of the code root: Windows refuses to delete a folder that is a working directory.
Set-Location -LiteralPath ([IO.Path]::GetTempPath())
$manifest = Import-GcManifest
if (-not $manifest.ContainsKey('settings')) {
    throw "no install record in $(Get-GcManifestPath): nothing this script can undo safely"
}
$recorded = $manifest['settings']
$settings = [pscustomobject]@{ CodeRoot = $recorded['CodeRoot']; DataRoot = $recorded['DataRoot']
    ConsoleUser = $recorded['ConsoleUser'] }

$failed = 0
$steps = @($script:GcSteps)
[array]::Reverse($steps)
foreach ($step in $steps) {
    $arguments = @{ Record = (Get-GcRecord $step.Name); Settings = $settings }
    if (Invoke-GcStep -Step $step -Function $step.Uninstall -Arguments $arguments) {
        Remove-GcRecord $step.Name
    } else {
        $failed++
    }
}

Write-Host ''
if ($failed) {
    Write-Host "== $failed step(s) failed; their record is kept, run this again once fixed" -ForegroundColor Yellow
    foreach ($warning in $script:GcWarnings) { Write-Host "   ! $warning" -ForegroundColor Yellow }
    exit 1
}
if (-not $script:GcDryRun) {
    Remove-Item -LiteralPath (Get-GcManifestPath) -Force -ErrorAction SilentlyContinue
}
Write-Host "== GameCore removed. Your data is still in $($settings.DataRoot)" -ForegroundColor Green
exit 0
