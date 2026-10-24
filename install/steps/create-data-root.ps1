# The data root: everything the player owns (ROMs, saves, covers, config).
# Created with the folders the backend does not make itself, the bundled themes
# seeded, and the console user allowed to write it. Never removed by uninstall.

# The bundled themes ship in the source's config\themes and live in the data
# root (backend/services/paths.py, "themes"): a theme is the player's to remove.
$script:GcDataFolders = @('config', 'config\themes', 'emu', 'emu\covers', 'logs')

function Grant-GcModify {
    <# Give $User modify rights on $Path, inherited by everything below. #>
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$User)
    $acl = Get-Acl -LiteralPath $Path
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $User, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Test-GcUserCanModify {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$User)
    $name = $User.Split('\')[-1]
    foreach ($rule in (Get-Acl -LiteralPath $Path).Access) {
        $who = $rule.IdentityReference.Value.Split('\')[-1]
        if ($who -ieq $name -and $rule.AccessControlType -eq 'Allow' -and
            ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Modify) -eq
            [Security.AccessControl.FileSystemRights]::Modify) {
            return $true
        }
    }
    return $false
}

function Get-GcThemesToSeed {
    <# Bundled themes whose folder is absent or empty in the data root: a populated one is the player's. #>
    param([Parameter(Mandatory)][string]$SourceRoot, [Parameter(Mandatory)][string]$DataRoot)
    $bundled = Join-Path $SourceRoot 'config\themes'
    if (-not (Test-Path -LiteralPath $bundled)) { return @() }
    $seed = @()
    foreach ($theme in (Get-ChildItem -LiteralPath $bundled -Directory)) {
        $target = Join-Path $DataRoot "config\themes\$($theme.Name)"
        $empty = -not (Test-Path -LiteralPath $target) -or
            -not (Get-ChildItem -LiteralPath $target -Force | Select-Object -First 1)
        if ($empty) { $seed += $theme.Name }
    }
    return $seed
}

function Install-GcDataRoot {
    param([Parameter(Mandatory)]$Settings)
    $data = $Settings.DataRoot
    if (-not (Test-Path -LiteralPath $data)) { Set-GcRecord 'create-data-root' 'created' $data }
    foreach ($folder in $script:GcDataFolders) {
        $path = Join-Path $data $folder
        if (-not (Test-Path -LiteralPath $path)) {
            Invoke-GcChange "create $path" { New-Item -ItemType Directory -Force -Path $path | Out-Null }
        }
    }
    foreach ($theme in (Get-GcThemesToSeed $Settings.SourceRoot $data)) {
        $from = Join-Path $Settings.SourceRoot "config\themes\$theme"
        $to = Join-Path $data "config\themes\$theme"
        Invoke-GcChange "seed the $theme theme" { Copy-Item -LiteralPath $from -Destination $to -Recurse -Force }
    }
    if ((Test-Path -LiteralPath $data) -and -not (Test-GcUserCanModify $data $Settings.ConsoleUser)) {
        Invoke-GcChange "let $($Settings.ConsoleUser) write $data" { Grant-GcModify $data $Settings.ConsoleUser }
    }
}

function Uninstall-GcDataRoot {
    param($Record, [Parameter(Mandatory)]$Settings)
    Write-GcInfo "the data root is the player's and stays: $($Settings.DataRoot)"
}
