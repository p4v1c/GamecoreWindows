# Caddy: the LAN's only door (HTTPS :8443, install/system/Caddyfile), as the
# GameCoreCaddy Windows service. A service, not part of the logon task: web
# access works before anyone signs in (dev log D8).

$script:GcCaddyVersion = '2.11.7'
$script:GcCaddyUri = "https://github.com/caddyserver/caddy/releases/download/v$script:GcCaddyVersion/caddy_$($script:GcCaddyVersion)_windows_amd64.zip"
# From caddy_2.11.7_checksums.txt, published with the release.
$script:GcCaddySha512 = 'c308154504e53755958ffc62d8a2657ab17eefd6dd25c45dbb2c3d93d29519971f77ca652f7d0f2bcb7f980b9ba3822478865e29969d0272d7613550a43152b1'
$script:GcCaddyService = 'GameCoreCaddy'

function Get-GcCaddyPaths {
    param([Parameter(Mandatory)][string]$CodeRoot)
    return [pscustomobject]@{
        Exe       = Join-Path $CodeRoot 'caddy\caddy.exe'
        Caddyfile = Join-Path $CodeRoot 'install\system\Caddyfile'
        # The local CA and its certificates: the box's identity, kept across reinstalls.
        Data      = Join-Path $script:GcStateDir 'caddy'
    }
}

function Get-GcCaddyCommandLine {
    param([Parameter(Mandatory)]$Paths)
    return "`"$($Paths.Exe)`" run --config `"$($Paths.Caddyfile)`" --adapter caddyfile"
}

function Get-GcCaddyEnvironment {
    <# Caddy reads XDG_* on Windows too; this keeps its CA out of SYSTEM's profile. #>
    param([Parameter(Mandatory)]$Paths)
    return @("XDG_DATA_HOME=$($Paths.Data)", "XDG_CONFIG_HOME=$($Paths.Data)")
}

function Get-GcInstalledCaddyVersion {
    param([Parameter(Mandatory)][string]$Exe)
    if (-not (Test-Path -LiteralPath $Exe)) { return $null }
    $line = (Invoke-GcNative $Exe @('version')) | Select-Object -First 1
    if ("$line" -match '^v?(\d+\.\d+\.\d+)') { return $Matches[1] }
    return $null
}

function Install-GcCaddyBinary {
    param([Parameter(Mandatory)][string]$Exe)
    $temp = Join-Path ([IO.Path]::GetTempPath()) "gamecore-caddy-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $temp | Out-Null
    try {
        $zip = Join-Path $temp 'caddy.zip'
        Save-GcDownload -Uri $script:GcCaddyUri -Destination $zip -Sha512 $script:GcCaddySha512
        Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $temp 'x')
        New-Item -ItemType Directory -Force -Path (Split-Path $Exe) | Out-Null
        Copy-Item -LiteralPath (Join-Path $temp 'x\caddy.exe') -Destination $Exe -Force
    } finally {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Install-GcCaddy {
    param([Parameter(Mandatory)]$Settings)
    $paths = Get-GcCaddyPaths $Settings.CodeRoot
    $name = $script:GcCaddyService
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    $restart = $false
    if ($null -ne $service -and (Get-GcInstalledCaddyVersion $paths.Exe) -ne $script:GcCaddyVersion) {
        Invoke-GcChange "stop $name to replace caddy.exe" { Stop-Service -Name $name -Force }
    }
    if ((Get-GcInstalledCaddyVersion $paths.Exe) -ne $script:GcCaddyVersion) {
        Invoke-GcChange "install Caddy $script:GcCaddyVersion in $(Split-Path $paths.Exe)" {
            Install-GcCaddyBinary $paths.Exe
        }
        $restart = $true
    }
    $commandLine = Get-GcCaddyCommandLine $paths
    if ($null -eq $service) {
        Set-GcRecord 'install-caddy' 'service' $name
        Invoke-GcChange "register the $name service" {
            New-Service -Name $name -BinaryPathName $commandLine -DisplayName 'GameCore Caddy' `
                -Description 'GameCore web access from the LAN (HTTPS :8443)' -StartupType Automatic | Out-Null
            # Restart after a crash, 5 s later, as the Linux unit did.
            Invoke-GcNative 'sc.exe' @('failure', $name, 'reset=', '86400', 'actions=', 'restart/5000') | Out-Null
        }
        $restart = $true
    }
    $key = "HKLM:\SYSTEM\CurrentControlSet\Services\$name"
    $environment = Get-GcCaddyEnvironment $paths
    $current = Get-GcRegistryValue $key 'Environment'
    if ($null -eq $current -or (@($current) -join '|') -ne ($environment -join '|')) {
        Invoke-GcChange "point $name at $($paths.Data)" {
            New-Item -ItemType Directory -Force -Path $paths.Data | Out-Null
            Set-GcRegistryValue $key 'Environment' $environment 'MultiString'
        }
        $restart = $true
    }
    if ($restart -or (Get-Service -Name $name -ErrorAction SilentlyContinue).Status -ne 'Running') {
        Invoke-GcChange "start $name" { Restart-Service -Name $name -Force }
    }
}

function Uninstall-GcCaddy {
    param($Record, [Parameter(Mandatory)]$Settings)
    $name = $script:GcCaddyService
    if ($null -eq (Get-Service -Name $name -ErrorAction SilentlyContinue)) { return }
    Invoke-GcChange "remove the $name service" {
        Stop-Service -Name $name -Force -ErrorAction SilentlyContinue
        Invoke-GcNative 'sc.exe' @('delete', $name) | Out-Null
    }
    Write-GcInfo "Caddy's CA stays in $((Get-GcCaddyPaths $Settings.CodeRoot).Data): phones that trust it keep working after a reinstall"
}
