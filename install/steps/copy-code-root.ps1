# The code root: the source tree, the built interface and the Electron shell,
# copied to Program Files. Read-only for the console user (Program Files' own
# ACL); the player's data never goes here.

# Never copied: version control, dependency trees the install rebuilds, the
# source's own venv, and anything the data root owns in a checkout.
$script:GcCodeExcludedDirs = @('.git', '.github', 'node_modules', '.venv', '__pycache__',
    '.pytest_cache', 'config', 'emu', 'logs', 'docs')

function Get-GcShellSource {
    param([Parameter(Mandatory)][string]$SourceRoot)
    return Join-Path $SourceRoot 'electron\dist\win-unpacked'
}

function Test-GcInterfaceBuilt {
    <# The two build outputs the code root needs: the frontend bundle and GameCore.exe. #>
    param([Parameter(Mandatory)][string]$SourceRoot)
    $bundle = Join-Path $SourceRoot 'frontend\dist\index.html'
    $shell = Join-Path (Get-GcShellSource $SourceRoot) 'GameCore.exe'
    return (Test-Path -LiteralPath $bundle) -and (Test-Path -LiteralPath $shell)
}

function Invoke-GcInterfaceBuild {
    <# Build the frontend and the shell in a checkout that has Node; a release archive ships them built. #>
    param([Parameter(Mandatory)][string]$SourceRoot)
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw 'the interface is not built and npm is not installed: install Node.js 22, or use a release archive'
    }
    foreach ($dir in 'frontend', 'electron') {
        Push-Location -LiteralPath (Join-Path $SourceRoot $dir)
        try {
            Invoke-GcNative 'npm.cmd' @('ci', '--no-audit', '--no-fund') | Out-Null
            $script = if ($dir -eq 'frontend') { 'build' } else { 'pack' }
            Invoke-GcNative 'npm.cmd' @('run', $script) | Out-Null
        } finally {
            Pop-Location
        }
    }
}

function Copy-GcTree {
    <# robocopy: only what differs is copied. Exit codes 0-7 are success. #>
    param([string]$From, [string]$To, [string[]]$ExcludeDirs = @())
    $arguments = @($From, $To, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:2', '/W:2')
    if ($ExcludeDirs.Count) { $arguments += @('/XD') + $ExcludeDirs }
    & robocopy.exe @arguments | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $From -> $To failed with $LASTEXITCODE" }
}

function Install-GcCodeRoot {
    param([Parameter(Mandatory)]$Settings)
    $source = $Settings.SourceRoot
    $code = $Settings.CodeRoot
    if ([IO.Path]::GetFullPath($source).TrimEnd('\') -ieq [IO.Path]::GetFullPath($code).TrimEnd('\')) {
        Write-GcInfo "running from the code root itself; nothing to copy"
        return
    }
    if (-not (Test-GcInterfaceBuilt $source)) {
        Invoke-GcChange "build the frontend and the shell in $source" { Invoke-GcInterfaceBuild $source }
    }
    if (-not (Test-Path -LiteralPath $code)) { Set-GcRecord 'copy-code-root' 'created' $code }
    $excluded = @($script:GcCodeExcludedDirs | ForEach-Object { Join-Path $source $_ })
    $excluded += Join-Path $source 'electron\dist'
    Invoke-GcChange "copy $source to $code" { Copy-GcTree $source $code $excluded }
    Invoke-GcChange "copy the shell to $code\shell" {
        Copy-GcTree (Get-GcShellSource $source) (Join-Path $code 'shell')
    }
}

function Uninstall-GcCodeRoot {
    param($Record)
    if ($null -eq $Record -or -not $Record.ContainsKey('created')) {
        Write-GcInfo "the code root was there before GameCore; left in place"
        return
    }
    $code = $Record['created']
    if (Test-Path -LiteralPath $code) {
        Invoke-GcChange "remove $code" { Remove-Item -LiteralPath $code -Recurse -Force }
    }
}
