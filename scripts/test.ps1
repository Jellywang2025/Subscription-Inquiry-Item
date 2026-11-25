#requires -Version 5.1
<#
.SYNOPSIS
    Shared Windows test entry: isolates temporary and personal state, then runs governance and full checks.
.PARAMETER TempRoot
    Optional temporary root. Defaults to the current system temp directory; restricted environments may pass the ignored repository directory .tmp.
#>
[CmdletBinding()]
param(
    [string]$TempRoot
)

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$runDir = $null
$logDir = $null
$createdTempRoot = $false

function Resolve-TempRoot {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    }
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Path))
}

function Resolve-Python {
    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        try {
            & $venvPython -c "import pytest, pyright" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return $venvPython
            }
        }
        catch {
            # Restricted environments may block this interpreter; try uv's bare Python below.
        }
    }

    $uvPythonRoot = Join-Path $env:APPDATA 'uv\python'
    if (Test-Path -LiteralPath $uvPythonRoot) {
        $candidate = Get-ChildItem -LiteralPath $uvPythonRoot -Directory -Filter 'cpython-3.12*-windows-x86_64-none' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'python.exe' } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if ($candidate) {
            $env:PYTHONPATH = Join-Path $repoRoot '.venv\Lib\site-packages'
            return $candidate
        }
    }
    return $null
}

function Resolve-GitBash {
    if ($env:GIT_BASH) {
        if (Test-Path -LiteralPath $env:GIT_BASH) {
            return $env:GIT_BASH
        }
        throw "GIT_BASH does not exist: $env:GIT_BASH"
    }
    $defaultBash = 'D:\Git\bin\bash.exe'
    if (Test-Path -LiteralPath $defaultBash) {
        return $defaultBash
    }
    $bashCommand = Get-Command bash -ErrorAction SilentlyContinue
    if ($bashCommand) {
        return $bashCommand.Source
    }
    throw 'Git Bash not found. Set GIT_BASH to bash.exe.'
}

function Remove-OwnedRunDirectory {
    param(
        [string]$Path,
        [string]$Root,
        [string]$Marker
    )
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullRoot = [System.IO.Path]::GetFullPath($Root)
    if (-not $fullRoot.EndsWith('\')) {
        $fullRoot += '\'
    }
    if (-not $fullPath.StartsWith($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the temporary root: $fullPath"
    }
    $owner = Join-Path $fullPath '.owner'
    if (-not [System.IO.File]::Exists($owner)) {
        throw "Refusing to clean a directory without an ownership marker: $fullPath"
    }
    if ([System.IO.File]::ReadAllText($owner) -ne $Marker) {
        throw "Refusing to clean a directory with a mismatched ownership marker: $fullPath"
    }
    [System.IO.Directory]::Delete($fullPath, $true)
}

function Write-Failure {
    param([string]$Step, [int]$Code)
    Write-Host ("Failed step: {0}, exit code: {1}" -f $Step, $Code) -ForegroundColor Red
    if ($logDir -and (Test-Path -LiteralPath $logDir)) {
        Write-Host ("Logs kept at: {0}" -f $logDir)
    }
}

function Invoke-CheckedStep {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments
    )
    Write-Host ("==> {0}" -f $Name)
    $log = Join-Path $logDir ("{0}.log" -f $Name)
    & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $log
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Add-Content -LiteralPath (Join-Path $logDir 'summary.log') -Value ("FAIL {0} exit={1}" -f $Name, $code)
        Write-Failure -Step $Name -Code $code
        Write-Host ("Log file: {0}" -f $log)
        exit $code
    }
    Add-Content -LiteralPath (Join-Path $logDir 'summary.log') -Value ("PASS {0}" -f $Name)
}

Push-Location -LiteralPath $repoRoot
try {
    $tempRootFull = Resolve-TempRoot $TempRoot
    if (-not [System.IO.Directory]::Exists($tempRootFull)) {
        [void][System.IO.Directory]::CreateDirectory($tempRootFull)
        $createdTempRoot = $true
    }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $runDir = Join-Path $tempRootFull ("quota-dashboard-tests-{0}-{1}" -f $stamp, $suffix)
    [void][System.IO.Directory]::CreateDirectory($runDir)
    [System.IO.File]::WriteAllText((Join-Path $runDir '.owner'), $runDir)

    $logDir = Join-Path $runDir 'logs'
    [void][System.IO.Directory]::CreateDirectory($logDir)

    $env:QUOTA_STATE_FILE = Join-Path $runDir 'quota-state.json'
    $env:QUOTA_CODEX_BASE_URL = 'http://127.0.0.1:1'
    $env:QUOTA_DEEPSEEK_BASE_URL = 'http://127.0.0.1:1'
    $env:QUOTA_MIMO_BASE_URL = 'http://127.0.0.1:1'
    $env:PYTHONDONTWRITEBYTECODE = '1'

    Write-Host ("Test run directory: {0}" -f $runDir)
    Write-Host ("Isolated QUOTA_STATE_FILE: {0}" -f $env:QUOTA_STATE_FILE)
    Write-Host 'Isolated service base URLs: Codex / DeepSeek / MiMo -> http://127.0.0.1:1'

    $python = Resolve-Python
    if (-not $python) {
        throw 'No usable Python 3.12 test environment. Run uv sync --locked in the repository root first.'
    }
    $venvRoot = Join-Path $repoRoot '.venv'
    if (Test-Path -LiteralPath $venvRoot) {
        $env:VIRTUAL_ENV = $venvRoot
        $env:PATH = (Join-Path $venvRoot 'Scripts') + ';' + $env:PATH
    }
    $bash = Resolve-GitBash
    $env:GIT_BASH = $bash
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) {
        throw 'node not found; node --check cannot run.'
    }

    Invoke-CheckedStep -Name 'governance' -FilePath $bash -Arguments @('scripts/check-governance.sh')
    Invoke-CheckedStep -Name 'pyright' -FilePath $python -Arguments @('-m', 'pyright')
    Invoke-CheckedStep -Name 'node-check' -FilePath $node.Source -Arguments @('--check', 'quota_dashboard/static/app.js')
    Invoke-CheckedStep -Name 'pytest' -FilePath $python -Arguments @(
        '-m', 'pytest',
        ('--basetemp=' + (Join-Path $runDir 'pytest')),
        '-p', 'no:cacheprovider'
    )

    Write-Host 'All checks passed.'
    Remove-OwnedRunDirectory -Path $runDir -Root $tempRootFull -Marker $runDir
    Write-Host ("Cleaned run directory: {0}" -f $runDir)
    if ($createdTempRoot -and [System.IO.Directory]::Exists($tempRootFull)) {
        if ([System.IO.Directory]::GetFileSystemEntries($tempRootFull).Count -eq 0) {
            [System.IO.Directory]::Delete($tempRootFull, $false)
            Write-Host ("Cleaned empty temp root created by this run: {0}" -f $tempRootFull)
        }
    }
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-Host ("Test entry error: {0}" -f $message) -ForegroundColor Red
    if ($logDir -and (Test-Path -LiteralPath $logDir)) {
        Add-Content -LiteralPath (Join-Path $logDir 'summary.log') -Value ("ERROR {0}" -f $message)
        Write-Host ("Logs kept at: {0}" -f $logDir)
    }
    exit 1
}
finally {
    Pop-Location
}
