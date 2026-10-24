# The offline metadata index: the LaunchBox dump (234 MB download, about
# 185 000 games) that gives a fresh install titles, synopses and covers with no
# account. Without it a box has no metadata source unless ScreenScraper keys
# were given. Never fails the install; MEDIA_INDEX=0 in install.conf skips it.

$script:GcIndexNeedMb = 700

function Get-GcMediaIndexLayout {
    param([Parameter(Mandatory)]$Settings)
    return [pscustomobject]@{
        Script = Join-Path $Settings.CodeRoot 'backend\services\gamemedia\gamescrape.py'
        Python = Join-Path $Settings.CodeRoot '.venv\Scripts\python.exe'
        Index  = Join-Path $Settings.DataRoot 'emu\gamescrape\launchbox.sqlite'
    }
}

function Get-GcFreeSpaceMb {
    param([Parameter(Mandatory)][string]$Path)
    $drive = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
    return [math]::Floor((New-Object IO.DriveInfo $drive).AvailableFreeSpace / 1MB)
}

function Invoke-GcMediaIndexBuild {
    <# gamescrape --refresh with both roots, so the index lands in the data root. #>
    param([Parameter(Mandatory)]$Settings, [Parameter(Mandatory)]$Paths)
    $saved = @{}
    $values = @{ GAMECORE_PATH = $Settings.CodeRoot; GAMECORE_DATA = $Settings.DataRoot; PYTHONUTF8 = '1' }
    foreach ($name in $values.Keys) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
    }
    try {
        Invoke-GcNative $Paths.Python @($Paths.Script, '--refresh') | Out-Null
    } finally {
        foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    }
}

function Test-GcIndexPresent {
    param([Parameter(Mandatory)][string]$Index)
    return (Test-Path -LiteralPath $Index) -and (Get-Item -LiteralPath $Index).Length -gt 0
}

function Install-GcMediaIndex {
    param([Parameter(Mandatory)]$Settings)
    $paths = Get-GcMediaIndexLayout $Settings
    $retry = ".\install\install.ps1 -Only build-media-index"
    if ((Get-GcSetting $Settings.Config 'MEDIA_INDEX' '1') -eq '0') {
        Write-GcInfo "MEDIA_INDEX=0: skipped. Build it later with $retry"
        return
    }
    if (Test-GcIndexPresent $paths.Index) { Write-GcInfo 'media index already present; kept'; return }
    if (-not $script:GcDryRun -and -not (Test-Path -LiteralPath $paths.Script)) {
        Write-GcWarning "gamescrape.py not found at $($paths.Script): media index skipped"
        return
    }
    $free = Get-GcFreeSpaceMb $Settings.DataRoot
    if ($free -lt $script:GcIndexNeedMb) {
        Write-GcWarning "only $free MB free for the data root, $script:GcIndexNeedMb MB needed: media index skipped; later: $retry"
        return
    }
    Invoke-GcChange 'download and index the LaunchBox dump (234 MB, 1-2 min)' {
        try {
            Invoke-GcMediaIndexBuild $Settings $paths
        } catch {
            Write-GcWarning "the media index could not be built (network? disk?): GameCore works without it; retry: $retry"
            return
        }
        if (-not (Test-GcIndexPresent $paths.Index)) {
            Write-GcWarning "gamescrape reported success but wrote no index; retry: $retry"
        }
    }
}

function Uninstall-GcMediaIndex {
    Write-GcInfo 'the media index is in the data root and stays with it'
}
