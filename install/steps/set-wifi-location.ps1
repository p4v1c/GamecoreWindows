# Windows 11 24H2 (build 26100) refuses a Wi-Fi scan to a desktop app that has
# no location access: Settings -> Wi-Fi would list nothing. Older builds scan
# without it, so they are left alone.

$script:GcWifiLocationBuild = 26100
$script:GcConsentStore = 'SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location'

function Get-GcLocationKeys {
    <# Device-wide, the user's switch, and the user's "let desktop apps" switch. #>
    return @(
        "HKLM:\$script:GcConsentStore",
        "HKCU:\$script:GcConsentStore",
        "HKCU:\$script:GcConsentStore\NonPackaged"
    )
}

function Install-GcWifiLocation {
    param([Parameter(Mandatory)]$Settings, [int]$Build = [Environment]::OSVersion.Version.Build)
    if ($Build -lt $script:GcWifiLocationBuild) {
        Write-GcInfo "build $Build scans Wi-Fi without location access; nothing to do"
        return
    }
    if ($env:USERNAME -ine $Settings.ConsoleUser.Split('\')[-1]) {
        Write-GcWarning ("location access is per user: sign in as $($Settings.ConsoleUser) and allow " +
            'Settings > Privacy > Location > Let desktop apps access your location, or the Wi-Fi scan stays empty')
        return
    }
    foreach ($key in (Get-GcLocationKeys)) {
        $value = Get-GcRegistryValue $key 'Value'
        if ($value -eq 'Allow') { continue }
        Set-GcRecord 'set-wifi-location' $key $value
        Invoke-GcChange "allow location access ($key)" { Set-GcRegistryValue $key 'Value' 'Allow' 'String' }
    }
}

function Uninstall-GcWifiLocation {
    param($Record, [Parameter(Mandatory)]$Settings)
    if ($null -eq $Record) { return }
    foreach ($key in @($Record.Keys)) {
        Invoke-GcChange "put location access back ($key)" {
            Restore-GcRegistryValue $key 'Value' $Record[$key] 'String'
        }
    }
}
