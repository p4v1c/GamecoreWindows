# session.env: what install/bin/gamecore-session hands the backend and the
# shell. Both roots, the port, and the scraper credentials, so the file is
# readable by administrators and SYSTEM only (the session runs elevated).

# install.conf key -> the variable the backend reads (same names as the Linux
# systemd drop-in wrote).
$script:GcSecretKeys = [ordered]@{
    TGDB_API_KEY    = 'THEGAMESDB_API_KEY'
    SS_DEV_ID       = 'SCREENSCRAPER_DEV_ID'
    SS_DEV_PASSWORD = 'SCREENSCRAPER_DEV_PASSWORD'
    SS_USER         = 'SCREENSCRAPER_USER'
    SS_PASSWORD     = 'SCREENSCRAPER_PASSWORD'
}

function Get-GcSessionEnvPath {
    return Join-Path $script:GcStateDir 'session.env'
}

function Get-GcSessionEnvText {
    <# The file's text. A value already in the file is kept when install.conf leaves it empty. #>
    param([Parameter(Mandatory)]$Settings, [hashtable]$Existing = @{})
    $lines = @(
        '# Written by install\steps\write-session-env.ps1; read by install\bin\gamecore-session.',
        '# Administrators only: it holds the scraper credentials.',
        "GAMECORE_PATH=$($Settings.CodeRoot)",
        "GAMECORE_DATA=$($Settings.DataRoot)",
        "GAMECORE_BACKEND_PORT=$($Settings.Port)"
    )
    foreach ($key in $script:GcSecretKeys.Keys) {
        $name = $script:GcSecretKeys[$key]
        $value = Get-GcSetting $Settings.Config $key ''
        if (-not $value -and $Existing.ContainsKey($name)) { $value = $Existing[$name] }
        if ($value -match "[`r`n]") { throw "$key holds a line break" }
        if ($value) { $lines += "$name=$value" }
    }
    return ($lines -join "`n") + "`n"
}

function Protect-GcAdminOnly {
    <# Administrators and SYSTEM, nothing inherited. #>
    param([Parameter(Mandatory)][string]$Path)
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in 'S-1-5-32-544', 'S-1-5-18') {
        $identity = New-Object Security.Principal.SecurityIdentifier $sid
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Install-GcSessionEnv {
    param([Parameter(Mandatory)]$Settings)
    $path = Get-GcSessionEnvPath
    $existing = @{}
    if (Test-Path -LiteralPath $path) {
        $existing = Read-GcConfigText ([IO.File]::ReadAllText($path))
    }
    $text = Get-GcSessionEnvText -Settings $Settings -Existing $existing
    $current = if (Test-Path -LiteralPath $path) { [IO.File]::ReadAllText($path) } else { $null }
    if ($current -eq $text) { return }
    Set-GcRecord 'write-session-env' 'path' $path
    Invoke-GcChange "write $path (administrators only)" {
        New-Item -ItemType Directory -Force -Path (Split-Path $path) | Out-Null
        if (-not (Test-Path -LiteralPath $path)) { [IO.File]::WriteAllText($path, '') }
        Protect-GcAdminOnly $path
        [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false))
    }
}

function Uninstall-GcSessionEnv {
    param($Record, [Parameter(Mandatory)]$Settings)
    $path = Get-GcSessionEnvPath
    if (Test-Path -LiteralPath $path) {
        Invoke-GcChange "remove $path" { Remove-Item -LiteralPath $path -Force }
    }
}
