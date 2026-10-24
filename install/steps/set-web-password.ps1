# The LAN login password (docs/SECURITY.md): asked once, stored hashed by the
# backend's own auth module in <data>\config\auth.json. An existing password is
# kept: delete auth.json and re-run the step to set a new one.

function Read-GcNewPassword {
    <# Ask twice at the console; the plain text never leaves this process. #>
    while ($true) {
        $first = Read-Host -AsSecureString '   Web password (cannot be empty)'
        $second = Read-Host -AsSecureString '   Confirm'
        $a = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($first))
        $b = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($second))
        if (-not $a) { Write-GcWarning 'an empty password is refused'; continue }
        if ($a -ceq $b) { return $a }
        Write-GcWarning 'the two passwords differ; try again'
    }
}

function Set-GcBackendPassword {
    <# Hand the password to auth.set_password() through the environment, never the command line. #>
    param([Parameter(Mandatory)]$Settings, [Parameter(Mandatory)][string]$Password)
    $python = Join-Path $Settings.CodeRoot '.venv\Scripts\python.exe'
    $code = 'import os, sys; sys.path.insert(0, os.environ["GAMECORE_PATH"]); ' +
        'from backend.services import auth; auth.set_password(os.environ.pop("GAMECORE_WEB_PASSWORD"))'
    $saved = @{}
    $values = @{ GAMECORE_PATH = $Settings.CodeRoot; GAMECORE_DATA = $Settings.DataRoot;
        GAMECORE_WEB_PASSWORD = $Password; PYTHONUTF8 = '1' }
    foreach ($name in $values.Keys) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
    }
    try {
        Invoke-GcNative $python @('-c', $code) | Out-Null
    } finally {
        foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    }
}

function Install-GcWebPassword {
    param([Parameter(Mandatory)]$Settings, [switch]$Unattended)
    $auth = Join-Path $Settings.DataRoot 'config\auth.json'
    if (Test-Path -LiteralPath $auth) {
        Write-GcInfo 'web password already set; kept'
        return
    }
    $password = Get-GcSetting $Settings.Config 'WEB_PASSWORD' ''
    if (-not $password -and $Unattended) {
        Write-GcWarning 'no WEB_PASSWORD in install.conf: web access stays locked until a password is set'
        return
    }
    Invoke-GcChange "set the web access password in $auth" {
        if (-not $password) { $password = Read-GcNewPassword }
        Set-GcBackendPassword -Settings $Settings -Password $password
    }
}

function Uninstall-GcWebPassword {
    param($Record, [Parameter(Mandatory)]$Settings)
    Write-GcInfo 'the web password is in the data root and stays with it'
}
