# Inbound rules on the Private profile only: web access (Caddy) and SSH.
# The backend itself binds 127.0.0.1 and gets no rule (docs/SECURITY.md).

function Get-GcFirewallRuleSpec {
    <# The rules this install wants, from its settings. #>
    param([Parameter(Mandatory)]$Settings)
    $rules = @([pscustomobject]@{ Name = 'GameCore - Web access'; Port = $Settings.WebPort })
    if ($Settings.Ssh) { $rules += [pscustomobject]@{ Name = 'GameCore - SSH'; Port = '22' } }
    return $rules
}

function Test-GcFirewallRule {
    <# True when a rule of that name already allows exactly that TCP port on Private. #>
    param([Parameter(Mandatory)]$Rule)
    $existing = Get-NetFirewallRule -DisplayName $Rule.Name -ErrorAction SilentlyContinue
    if ($null -eq $existing) { return $false }
    $filter = $existing | Get-NetFirewallPortFilter
    return (@($existing).Count -eq 1) -and ($existing.Enabled -eq 'True') -and
        ("$($existing.Profile)" -eq 'Private') -and ("$($filter.LocalPort)" -eq "$($Rule.Port)") -and
        ("$($filter.Protocol)" -eq 'TCP')
}

function Install-GcFirewall {
    param([Parameter(Mandatory)]$Settings)
    foreach ($rule in (Get-GcFirewallRuleSpec $Settings)) {
        if (Test-GcFirewallRule $rule) { continue }
        Set-GcRecord 'configure-firewall' $rule.Name $true
        Invoke-GcChange "allow TCP $($rule.Port) on private networks ($($rule.Name))" {
            Remove-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
            New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow -Protocol TCP `
                -LocalPort $rule.Port -Profile Private -Group 'GameCore' | Out-Null
        }
    }
}

function Uninstall-GcFirewall {
    param($Record)
    if ($null -eq $Record) { return }
    foreach ($name in @($Record.Keys)) {
        Invoke-GcChange "remove the firewall rule $name" {
            Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
        }
    }
}
