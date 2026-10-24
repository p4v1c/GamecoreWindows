# OpenSSH Server (the Windows optional feature), started automatically, as the
# Linux box had sshd. Skipped with SSH=0 in install.conf.

$script:GcSshCapability = 'OpenSSH.Server~~~~0.0.1.0'
# Windows' own rule for sshd allows every profile; GameCore's rule is Private only.
$script:GcSshWindowsRule = 'OpenSSH-Server-In-TCP'

function Install-GcOpenSsh {
    param([Parameter(Mandatory)]$Settings)
    if (-not $Settings.Ssh) {
        Write-GcInfo 'SSH=0: skipped'
        return
    }
    $state = 'Unknown'
    try {
        $state = "$((Get-WindowsCapability -Online -Name $script:GcSshCapability).State)"
    } catch {
        # Reading optional features needs elevation; a dry run has none.
        if (-not $script:GcDryRun) { throw }
        Write-GcInfo 'OpenSSH Server state unreadable without elevation'
    }
    if ($state -ne 'Installed') {
        Set-GcRecord 'install-openssh' 'capability' $script:GcSshCapability
        Invoke-GcChange 'install OpenSSH Server (Windows optional feature)' {
            Add-WindowsCapability -Online -Name $script:GcSshCapability | Out-Null
        }
    }
    $service = Get-Service -Name sshd -ErrorAction SilentlyContinue
    if ($null -eq $service -or "$($service.StartType)" -ne 'Automatic') {
        if ($null -ne $service) { Set-GcRecord 'install-openssh' 'startType' "$($service.StartType)" }
        Invoke-GcChange 'start sshd with Windows' { Set-Service -Name sshd -StartupType Automatic }
    }
    if ($null -eq $service -or "$($service.Status)" -ne 'Running') {
        Invoke-GcChange 'start sshd' { Start-Service -Name sshd }
    }
    $rule = Get-NetFirewallRule -Name $script:GcSshWindowsRule -ErrorAction SilentlyContinue
    if ($null -ne $rule -and "$($rule.Enabled)" -eq 'True') {
        Set-GcRecord 'install-openssh' 'windowsRuleEnabled' $true
        Invoke-GcChange "disable Windows' all-profile sshd rule (GameCore - SSH allows Private only)" {
            Disable-NetFirewallRule -Name $script:GcSshWindowsRule
        }
    }
}

function Uninstall-GcOpenSsh {
    param($Record, [Parameter(Mandatory)]$Settings)
    if ($null -eq $Record) { return }
    if ($Record.ContainsKey('windowsRuleEnabled')) {
        Invoke-GcChange "re-enable Windows' sshd rule" {
            Enable-NetFirewallRule -Name $script:GcSshWindowsRule -ErrorAction SilentlyContinue
        }
    }
    if ($Record.ContainsKey('capability')) {
        Invoke-GcChange 'remove OpenSSH Server (GameCore installed it)' {
            Stop-Service -Name sshd -Force -ErrorAction SilentlyContinue
            Remove-WindowsCapability -Online -Name $Record['capability'] | Out-Null
        }
    } elseif ($Record.ContainsKey('startType')) {
        Invoke-GcChange "put sshd back to $($Record['startType'])" {
            Set-Service -Name sshd -StartupType $Record['startType']
        }
    }
}
