# The install steps, in the order install.ps1 runs them; uninstall.ps1 runs
# them backwards. One file per step in install\steps (docs/architecture/11-install-script-seams.md).

$script:GcSteps = @(
    [pscustomobject]@{ Name = 'copy-code-root';        Install = 'Install-GcCodeRoot';    Uninstall = 'Uninstall-GcCodeRoot' }
    [pscustomobject]@{ Name = 'install-python';        Install = 'Install-GcPython';      Uninstall = 'Uninstall-GcPython' }
    [pscustomobject]@{ Name = 'create-data-root';      Install = 'Install-GcDataRoot';    Uninstall = 'Uninstall-GcDataRoot' }
    [pscustomobject]@{ Name = 'write-session-env';     Install = 'Install-GcSessionEnv';  Uninstall = 'Uninstall-GcSessionEnv' }
    [pscustomobject]@{ Name = 'set-web-password';      Install = 'Install-GcWebPassword'; Uninstall = 'Uninstall-GcWebPassword' }
    [pscustomobject]@{ Name = 'install-caddy';         Install = 'Install-GcCaddy';       Uninstall = 'Uninstall-GcCaddy' }
    [pscustomobject]@{ Name = 'configure-firewall';    Install = 'Install-GcFirewall';    Uninstall = 'Uninstall-GcFirewall' }
    [pscustomobject]@{ Name = 'install-openssh';       Install = 'Install-GcOpenSsh';     Uninstall = 'Uninstall-GcOpenSsh' }
    [pscustomobject]@{ Name = 'configure-power';       Install = 'Install-GcPower';       Uninstall = 'Uninstall-GcPower' }
    [pscustomobject]@{ Name = 'set-wifi-location';     Install = 'Install-GcWifiLocation'; Uninstall = 'Uninstall-GcWifiLocation' }
    [pscustomobject]@{ Name = 'configure-autologon';   Install = 'Install-GcAutologon';   Uninstall = 'Uninstall-GcAutologon' }
    [pscustomobject]@{ Name = 'register-session-task'; Install = 'Install-GcSessionTask'; Uninstall = 'Uninstall-GcSessionTask' }
    # Last: the long download runs after every question has been asked.
    [pscustomobject]@{ Name = 'build-media-index';     Install = 'Install-GcMediaIndex';  Uninstall = 'Uninstall-GcMediaIndex' }
)

foreach ($step in $script:GcSteps) {
    . (Join-Path $PSScriptRoot "..\steps\$($step.Name).ps1")
}

function Select-GcStep {
    <# All steps, or the ones -Only names; an unknown name is refused. #>
    param([string[]]$Only)
    if (-not $Only) { return $script:GcSteps }
    $known = $script:GcSteps | ForEach-Object { $_.Name }
    foreach ($name in $Only) {
        if ($known -notcontains $name) { throw "unknown step '$name'; steps: $($known -join ', ')" }
    }
    return $script:GcSteps | Where-Object { $Only -contains $_.Name }
}

function Invoke-GcStep {
    <# Run one step's install or uninstall function; a failure is reported, never fatal. #>
    param([Parameter(Mandatory)]$Step, [Parameter(Mandatory)][string]$Function, [hashtable]$Arguments)
    Write-GcStep $Step.Name
    try {
        $command = Get-Command $Function
        $call = @{}
        foreach ($key in $Arguments.Keys) {
            if ($command.Parameters.ContainsKey($key)) { $call[$key] = $Arguments[$key] }
        }
        & $Function @call
        return $true
    } catch {
        Write-GcWarning "$($Step.Name) failed: $($_.Exception.Message)"
        return $false
    }
}
