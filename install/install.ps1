<#
.SYNOPSIS
    Install GameCore for Windows, or bring an install up to date.

.DESCRIPTION
    Copies the code to Program Files, installs Python and the backend's
    packages, prepares the data root, Caddy (web access), OpenSSH, the firewall
    rules, the power settings a console needs, automatic sign-in and the logon
    task that starts GameCore. Re-running it changes only what differs.
    Elevates itself (UAC). Every change is recorded for uninstall.ps1.

.EXAMPLE
    .\install\install.ps1
    Interactive: asks where the data goes, the web password and the Windows password for automatic sign-in.

.EXAMPLE
    .\install\install.ps1 -ConfigFile install.conf -Unattended
    Scripted, from install\install.conf.example. Automatic sign-in is left for the owner.

.EXAMPLE
    .\install\install.ps1 -DryRun
    Prints what would change and changes nothing; needs no elevation.
#>
[CmdletBinding()]
param(
    [string]$ConfigFile,
    [switch]$Unattended,
    [switch]$DryRun,
    [string[]]$Only,
    [string]$CodeRoot,
    [string]$DataRoot,
    [string]$ConsoleUser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')
. (Join-Path $PSScriptRoot 'lib\steps.ps1')

$MinimumBuild = 17763  # Windows 10 1809: the oldest build with the APIs the platform layer calls

function Get-GcInstallConfig {
    <# install.conf, then the command line over it. #>
    param([string]$File, [string]$Code, [string]$Data, [string]$User)
    $config = @{}
    if ($File) { $config = Read-GcConfig -Path (Resolve-Path -LiteralPath $File) }
    if ($Code) { $config['GAMECORE_PATH'] = $Code }
    if ($Data) { $config['GAMECORE_DATA'] = $Data }
    if ($User) { $config['USER_NAME'] = $User }
    return $config
}

function Request-GcDataRoot {
    <# Interactive only: offer the default, accept another folder (an NTFS partition such as D:\GameCoreData). #>
    param([Parameter(Mandatory)]$Settings)
    if ($Unattended -or $DryRun -or $DataRoot -or $Settings.Config.ContainsKey('GAMECORE_DATA')) { return $Settings }
    $answer = Read-Host "   Where should ROMs, saves and covers go? [$($Settings.DataRoot)]"
    if ($answer.Trim()) {
        Assert-GcDataOutsideCode -CodeRoot $Settings.CodeRoot -DataRoot $answer.Trim()
        $Settings.DataRoot = $answer.Trim()
    }
    return $Settings
}

function Start-GcElevated {
    <# Re-run this script as administrator with the settings resolved here, in the console user's context. #>
    param([Parameter(Mandatory)]$Settings)
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
        '-CodeRoot', "`"$($Settings.CodeRoot)`"", '-DataRoot', "`"$($Settings.DataRoot)`"",
        '-ConsoleUser', "`"$($Settings.ConsoleUser)`"")
    if ($ConfigFile) { $arguments += @('-ConfigFile', "`"$((Resolve-Path -LiteralPath $ConfigFile).Path)`"") }
    if ($Unattended) { $arguments += '-Unattended' }
    if ($Only) { $arguments += @('-Only', ($Only -join ',')) }
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    return $process.ExitCode
}

function Write-GcSummary {
    param([Parameter(Mandatory)]$Settings)
    Write-Host ''
    $verb = if ($script:GcDryRun) { 'would change' } else { 'changed' }
    Write-Host "== Summary: $verb $($script:GcChanges.Count) thing(s)" -ForegroundColor Cyan
    Write-Host "   code  $($Settings.CodeRoot)"
    Write-Host "   data  $($Settings.DataRoot)"
    foreach ($warning in $script:GcWarnings) { Write-Host "   ! $warning" -ForegroundColor Yellow }
    if (-not $script:GcDryRun -and -not $script:GcWarnings.Count) {
        Write-Host '   Restart the PC: it signs in and starts GameCore.' -ForegroundColor Green
    }
}

if ([Environment]::OSVersion.Version.Build -lt $MinimumBuild -or -not [Environment]::Is64BitOperatingSystem) {
    throw "GameCore needs 64-bit Windows 10 1809 (build $MinimumBuild) or later"
}
$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$config = Get-GcInstallConfig -File $ConfigFile -Code $CodeRoot -Data $DataRoot -User $ConsoleUser
$settings = Resolve-GcConfiguration -Config $config -SourceRoot $sourceRoot
$settings = Request-GcDataRoot $settings
if ($settings.DataRoot -match '\\OneDrive') {
    Write-GcWarning "the data root $($settings.DataRoot) is synced by OneDrive: ROMs and saves would upload; choose another folder with -DataRoot"
}

$script:GcDryRun = [bool]$DryRun
if (-not $DryRun -and -not (Test-GcElevated)) {
    exit (Start-GcElevated $settings)
}

Import-GcManifest | Out-Null
$script:GcManifest['settings'] = @{ CodeRoot = $settings.CodeRoot; DataRoot = $settings.DataRoot
    ConsoleUser = $settings.ConsoleUser }
Save-GcManifest
$failed = 0
$arguments = @{ Settings = $settings; Unattended = [bool]$Unattended }
foreach ($step in (Select-GcStep -Only ($Only -split ',' | Where-Object { $_ }))) {
    if (-not (Invoke-GcStep -Step $step -Function $step.Install -Arguments $arguments)) { $failed++ }
}
Write-GcSummary $settings
if ($failed) { exit 1 }
exit 0
