# Helpers shared by install.ps1, uninstall.ps1 and every install/steps/*.ps1.
#
# Every change to the machine goes through Invoke-GcChange, so a dry run lists
# the plan and changes nothing, and every native program through
# Invoke-GcNative, so the tests replace both.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:GcDryRun = $false
$script:GcChanges = New-Object System.Collections.Generic.List[string]
$script:GcWarnings = New-Object System.Collections.Generic.List[string]
$script:GcRestartReasons = New-Object System.Collections.Generic.List[string]
$script:GcManifest = @{}

# The install's own record: what each step changed and the values it replaced.
# uninstall.ps1 reads it; a re-run keeps the first values, never its own.
# GAMECORE_STATE_DIR moves it for the tests.
$script:GcStateDir = if ($env:GAMECORE_STATE_DIR) { $env:GAMECORE_STATE_DIR } else { Join-Path $env:ProgramData 'GameCore' }

function Write-GcStep {
    param([Parameter(Mandatory)][string]$Name)
    Write-Host ''
    Write-Host "== $Name" -ForegroundColor Cyan
}

function Write-GcInfo {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "   $Message"
}

function Write-GcWarning {
    param([Parameter(Mandatory)][string]$Message)
    $script:GcWarnings.Add($Message)
    Write-Host "   ! $Message" -ForegroundColor Yellow
}

function Add-GcRestartReason {
    <# A change that only applies after a restart; the summary lists them. The installer never restarts. #>
    param([Parameter(Mandatory)][string]$Reason)
    $script:GcRestartReasons.Add($Reason)
}

function Get-GcEditionId {
    <# The Windows edition: Core (Home), Professional, Enterprise, EnterpriseS (LTSC), Education, IoTEnterprise... #>
    return Get-GcRegistryValue 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' 'EditionID'
}

function Invoke-GcChange {
    <# Run $GcChangeAction unless this is a dry run; either way the change is listed. #>
    # Prefixed names: the block runs in this scope, so a parameter called
    # $Action would hide the caller's $action inside it.
    param(
        [Parameter(Mandatory)][string]$GcChangeDescription,
        [Parameter(Mandatory)][scriptblock]$GcChangeAction
    )
    $script:GcChanges.Add($GcChangeDescription)
    if ($script:GcDryRun) {
        Write-Host "   would: $GcChangeDescription" -ForegroundColor DarkGray
        return
    }
    Write-Host "   $GcChangeDescription"
    & $GcChangeAction
}

function Invoke-GcNative {
    <# Run a native program; a non-zero exit code throws with its output. #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    # Windows PowerShell turns each stderr line into an error record, and under
    # 'Stop' the first one (a pip warning) throws although the program succeeded.
    $ErrorActionPreference = 'Continue'
    $output = @(& $FilePath @ArgumentList 2>&1)
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath $($ArgumentList -join ' ') exited with $LASTEXITCODE`: $(($output | ForEach-Object { "$_" }) -join ' ')"
    }
    # stdout only: callers parse it (a version line), and a warning is not an answer.
    return @($output | Where-Object { $_ -isnot [Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
}

function Test-GcElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal $identity
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Read-GcConfigText {
    <# KEY=value lines of install.conf; quotes stripped, `#` comments dropped. #>
    param([AllowEmptyString()][string]$Text)
    $values = @{}
    foreach ($raw in ($Text -split "`r?`n")) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
        $key, $value = $line.Split('=', 2)
        $value = $value.Trim()
        if ($value -match '^"([^"]*)"' -or $value -match "^'([^']*)'") {
            $value = $Matches[1]
        } else {
            $value = ($value -replace '\s+#.*$', '').Trim()
        }
        $values[$key.Trim()] = $value
    }
    return $values
}

function Read-GcConfig {
    param([Parameter(Mandatory)][string]$Path)
    return Read-GcConfigText -Text ([IO.File]::ReadAllText($Path))
}

function Get-GcSetting {
    param([hashtable]$Config, [string]$Key, [string]$Default = '')
    if ($Config.ContainsKey($Key) -and $Config[$Key] -ne '') { return $Config[$Key] }
    return $Default
}

function Assert-GcDataOutsideCode {
    <# The data root may not be the code root or inside it: an update replaces the code root whole. #>
    param([Parameter(Mandatory)][string]$CodeRoot, [Parameter(Mandatory)][string]$DataRoot)
    if (-not [IO.Path]::IsPathRooted($DataRoot)) { throw "GAMECORE_DATA must be a full path, not '$DataRoot'" }
    $code = [IO.Path]::GetFullPath($CodeRoot).TrimEnd('\') + '\'
    $data = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\') + '\'
    if ($data.StartsWith($code, [StringComparison]::OrdinalIgnoreCase)) {
        throw "the data root $DataRoot is inside the code root $CodeRoot, which an update replaces: choose another folder"
    }
}

function Resolve-GcConfiguration {
    <#
    The install's settings from install.conf (or prompts) with Windows defaults:
    code in Program Files, data in the console user's Documents (dev log D6).
    #>
    param([hashtable]$Config, [Parameter(Mandatory)][string]$SourceRoot)
    $documents = [Environment]::GetFolderPath('MyDocuments')
    # Empty when the profile has no Documents folder yet (a fresh account).
    if (-not $documents) { $documents = Join-Path $env:USERPROFILE 'Documents' }
    $port = Get-GcSetting $Config 'WEB_PORT' '8765'
    if ($port -notmatch '^\d{2,5}$') { throw "WEB_PORT must be a port number, not '$port'" }
    $code = Get-GcSetting $Config 'GAMECORE_PATH' (Join-Path $env:ProgramFiles 'GameCore')
    $data = Get-GcSetting $Config 'GAMECORE_DATA' (Join-Path $documents 'GameCore')
    Assert-GcDataOutsideCode -CodeRoot $code -DataRoot $data
    return [pscustomobject]@{
        SourceRoot  = $SourceRoot
        CodeRoot    = $code
        DataRoot    = $data
        ConsoleUser = Get-GcSetting $Config 'USER_NAME' $env:USERNAME
        Port        = $port
        WebPort     = Get-GcSetting $Config 'LAN_PORT' '8443'
        Autologon   = (Get-GcSetting $Config 'AUTOLOGON' '1') -eq '1'
        Ssh         = (Get-GcSetting $Config 'SSH' '1') -eq '1'
        Kiosk       = (Get-GcSetting $Config 'KIOSK' '1') -eq '1'
        QuietBoot   = (Get-GcSetting $Config 'QUIET_BOOT' '1') -eq '1'
        Config      = $Config
    }
}

function ConvertTo-GcHashtable {
    <# ConvertFrom-Json gives PSCustomObjects in Windows PowerShell 5.1. #>
    param($InputObject)
    if ($null -eq $InputObject) { return $null }
    if ($InputObject -is [System.Management.Automation.PSCustomObject]) {
        $table = @{}
        foreach ($property in $InputObject.PSObject.Properties) {
            $table[$property.Name] = ConvertTo-GcHashtable $property.Value
        }
        return $table
    }
    if ($InputObject -is [System.Collections.IEnumerable] -and $InputObject -isnot [string]) {
        return @($InputObject | ForEach-Object { ConvertTo-GcHashtable $_ })
    }
    return $InputObject
}

function Get-GcManifestPath {
    return Join-Path $script:GcStateDir 'install-manifest.json'
}

function Import-GcManifest {
    $path = Get-GcManifestPath
    $script:GcManifest = @{}
    if (Test-Path -LiteralPath $path) {
        $script:GcManifest = ConvertTo-GcHashtable ([IO.File]::ReadAllText($path) | ConvertFrom-Json)
    }
    return $script:GcManifest
}

function Save-GcManifest {
    if ($script:GcDryRun) { return }
    $path = Get-GcManifestPath
    New-Item -ItemType Directory -Force -Path (Split-Path $path) | Out-Null
    $json = $script:GcManifest | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($path, $json, (New-Object Text.UTF8Encoding $false))
}

function Get-GcRecord {
    <# What a step recorded on its first run, or $null. #>
    param([Parameter(Mandatory)][string]$Step)
    if ($script:GcManifest.ContainsKey($Step)) { return $script:GcManifest[$Step] }
    return $null
}

function Set-GcRecord {
    <# Note what a step replaced. A value already noted is kept: it is the original. #>
    param(
        [Parameter(Mandatory)][string]$Step,
        [Parameter(Mandatory)][string]$Key,
        $Value
    )
    if (-not $script:GcManifest.ContainsKey($Step)) { $script:GcManifest[$Step] = @{} }
    if (-not $script:GcManifest[$Step].ContainsKey($Key)) {
        $script:GcManifest[$Step][$Key] = $Value
        Save-GcManifest
    }
}

function Remove-GcRecord {
    param([Parameter(Mandatory)][string]$Step)
    if ($script:GcManifest.ContainsKey($Step)) {
        $script:GcManifest.Remove($Step)
        Save-GcManifest
    }
}

function Get-GcRegistryValue {
    <# A registry value, or $null when the key or the value is absent. #>
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
    $item = Get-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    return $item.$Name
}

function Set-GcRegistryValue {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)]$Value,
        [string]$Type = 'DWord'
    )
    if (-not (Test-Path -LiteralPath $Path)) { New-GcRegistryKey $Path }
    New-ItemProperty -LiteralPath $Path -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
}

function New-GcRegistryKey {
    <#
    Create a missing key. HKLM goes through .NET's 64-bit view: the provider's
    New-Item -Force failed elevated on HKLM\SOFTWARE\GameCore on the owner's PC
    ("Requested registry access is not allowed").
    #>
    param([Parameter(Mandatory)][string]$Path)
    if ($Path -notmatch '^HKLM:\\(.+)$') { New-Item -Path $Path -Force | Out-Null; return }
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine', 'Registry64')
    try { $base.CreateSubKey($Matches[1]).Close() } finally { $base.Close() }
}

function Restore-GcRegistryValue {
    <# Put back what was there; $null means there was nothing, so remove it. #>
    param([string]$Path, [string]$Name, $Previous, [string]$Type = 'DWord')
    if ($null -eq $Previous) {
        Remove-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction SilentlyContinue
    } else {
        Set-GcRegistryValue -Path $Path -Name $Name -Value $Previous -Type $Type
    }
}

function Save-GcDownload {
    <# Download $Uri to $Destination and check its SHA-512; a mismatch deletes it and throws. #>
    param(
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][string]$Destination,
        [Parameter(Mandatory)][string]$Sha512
    )
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing
    $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA512).Hash
    if ($actual -ne $Sha512.ToUpperInvariant()) {
        Remove-Item -LiteralPath $Destination -Force
        throw "$Uri does not match its pinned SHA-512 (got $actual)"
    }
}
