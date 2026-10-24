# Python for the backend: CPython 3.12 from its NuGet package into
# <code>\python, then a venv at <code>\.venv with backend/requirements.txt.
#
# The NuGet package rather than the embeddable zip: the embeddable build has no
# venv and no pip, and a venv is what the shell and the session expect
# (dev log D3, D39).

$script:GcPythonVersion = '3.12.10'
$script:GcPythonUri = "https://api.nuget.org/v3-flatcontainer/python/$script:GcPythonVersion/python.$script:GcPythonVersion.nupkg"
# packageHash of python.3.12.10.nupkg in the NuGet catalogue, as hex.
$script:GcPythonSha512 = 'bbda4dcf688a94211b62d50968a91b38f305d0b8d1ecd90269f74a86f8a0a4fcebb7ca162a0753a47691eb3df0c964009bd3d8194c6fd19afae8d5fd01e1cc0f'

function Get-GcPythonPaths {
    param([Parameter(Mandatory)][string]$CodeRoot)
    return [pscustomobject]@{
        Home         = Join-Path $CodeRoot 'python'
        Interpreter  = Join-Path $CodeRoot 'python\tools\python.exe'
        Venv         = Join-Path $CodeRoot '.venv'
        VenvPython   = Join-Path $CodeRoot '.venv\Scripts\python.exe'
        Requirements = Join-Path $CodeRoot 'backend\requirements.txt'
        Stamp        = Join-Path $CodeRoot '.venv\gamecore-requirements.sha256'
    }
}

function Get-GcInstalledPythonVersion {
    param([Parameter(Mandatory)][string]$Interpreter)
    if (-not (Test-Path -LiteralPath $Interpreter)) { return $null }
    return ((Invoke-GcNative $Interpreter @('-c', 'import platform; print(platform.python_version())')) | Select-Object -Last 1).ToString().Trim()
}

function Expand-GcPythonPackage {
    param([Parameter(Mandatory)][string]$Destination)
    $temp = Join-Path ([IO.Path]::GetTempPath()) "gamecore-python-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $temp | Out-Null
    try {
        $package = Join-Path $temp 'python.zip'
        Save-GcDownload -Uri $script:GcPythonUri -Destination $package -Sha512 $script:GcPythonSha512
        if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
        Expand-Archive -LiteralPath $package -DestinationPath $Destination
    } finally {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Install-GcPython {
    param([Parameter(Mandatory)]$Settings)
    $paths = Get-GcPythonPaths $Settings.CodeRoot
    $version = Get-GcInstalledPythonVersion $paths.Interpreter
    if ($version -ne $script:GcPythonVersion) {
        Invoke-GcChange "install Python $script:GcPythonVersion in $($paths.Home)" {
            Expand-GcPythonPackage $paths.Home
        }
    }
    if (-not (Test-Path -LiteralPath $paths.VenvPython) -or $version -ne $script:GcPythonVersion) {
        Invoke-GcChange "create the venv $($paths.Venv)" {
            Invoke-GcNative $paths.Interpreter @('-m', 'venv', '--clear', $paths.Venv) | Out-Null
        }
    }
    $wanted = if (Test-Path -LiteralPath $paths.Requirements) {
        (Get-FileHash -LiteralPath $paths.Requirements -Algorithm SHA256).Hash
    } else { 'absent' }
    $installed = if (Test-Path -LiteralPath $paths.Stamp) { [IO.File]::ReadAllText($paths.Stamp).Trim() } else { '' }
    if ($installed -ne $wanted) {
        Invoke-GcChange "install the backend's Python packages" {
            Invoke-GcNative $paths.VenvPython @('-m', 'pip', 'install', '--disable-pip-version-check',
                '--no-input', '-q', '-r', $paths.Requirements) | Out-Null
            [IO.File]::WriteAllText($paths.Stamp, $wanted)
        }
    }
}

function Uninstall-GcPython {
    param($Record, [Parameter(Mandatory)]$Settings)
    # Both live in the code root, which copy-code-root removes.
    Write-GcInfo 'Python and the venv leave with the code root'
}
