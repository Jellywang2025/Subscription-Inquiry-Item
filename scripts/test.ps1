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

function Get-NormalizedPath {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $root.Length) {
        $full = $full.TrimEnd([char[]]@('\', '/'))
    }
    return $full
}

function Test-PathWithin {
    param([string]$Path, [string]$Root)
    $fullPath = Get-NormalizedPath $Path
    $fullRoot = Get-NormalizedPath $Root
    if ($fullPath.Equals($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $fullRoot.TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
    return $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Resolve-TempRoot {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return Get-NormalizedPath ([System.IO.Path]::GetTempPath())
    }
    $candidate = if ([System.IO.Path]::IsPathRooted($Path)) {
        $Path
    }
    else {
        Join-Path -Path $repoRoot -ChildPath $Path
    }
    $fullPath = Get-NormalizedPath $candidate
    $repoRootFull = Get-NormalizedPath $repoRoot
    if (Test-PathWithin -Path $fullPath -Root $repoRootFull) {
        $unifiedTemp = Get-NormalizedPath (Join-Path -Path $repoRoot -ChildPath '.tmp')
        if (-not (Test-PathWithin -Path $fullPath -Root $unifiedTemp)) {
            throw "TempRoot must be the repository .tmp directory or outside the repository: $fullPath"
        }
    }
    return $fullPath
}

function Assert-NoReparsePoints {
    param([string]$Path)
    $fullPath = Get-NormalizedPath $Path
    $root = [System.IO.Path]::GetPathRoot($fullPath)
    $current = $root
    $segments = $fullPath.Substring($root.Length).Split(
        [char[]]@('\', '/'),
        [System.StringSplitOptions]::RemoveEmptyEntries
    )
    foreach ($segment in $segments) {
        $current = Join-Path -Path $current -ChildPath $segment
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to clean through a reparse point: $current"
        }
    }
}

function Resolve-Python {
    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        try {
            $global:LASTEXITCODE = $null
            & $venvPython -c "import pytest, pyright" 2>$null | Out-Null
            if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -eq 0) {
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
            $global:LASTEXITCODE = $null
            & $candidate -c "import pytest, pyright" 2>$null | Out-Null
            if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -eq 0) {
                return $candidate
            }
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
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        $gitRoot = Split-Path -Parent (Split-Path -Parent $git.Source)
        $gitBash = Join-Path $gitRoot 'bin\bash.exe'
        if (Test-Path -LiteralPath $gitBash -PathType Leaf) {
            return $gitBash
        }
    }
    foreach ($bash in Get-Command bash.exe -All -ErrorAction SilentlyContinue) {
        if ($bash.Source -match '[\\/]Git[\\/](?:usr[\\/])?bin[\\/]bash\.exe$') {
            return $bash.Source
        }
    }
    throw 'Git Bash not found. Set GIT_BASH to bash.exe.'
}

function Remove-OwnedRunDirectory {
    param(
        [string]$Path,
        [string]$Root,
        [string]$Marker
    )
    $fullPath = Get-NormalizedPath $Path
    $fullRoot = Get-NormalizedPath $Root
    $outsideRoot = -not (Test-PathWithin -Path $fullPath -Root $fullRoot)
    if ($outsideRoot -or $fullPath.Equals($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the temporary root: $fullPath"
    }
    $runItem = Get-Item -LiteralPath $fullPath -Force -ErrorAction Stop
    if (-not $runItem.PSIsContainer) {
        throw "Refusing to clean a non-directory: $fullPath"
    }
    # 物理路径中的任一层都不得是重解析点，避免删除被重定向到临时根之外。
    Assert-NoReparsePoints -Path $fullPath
    $physicalPath = Get-NormalizedPath $runItem.FullName
    if (-not $physicalPath.Equals($fullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a directory whose physical path differs: $physicalPath"
    }
    $owner = Join-Path -Path $fullPath -ChildPath '.owner'
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
    $global:LASTEXITCODE = $null
    & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $log
    $code = if ($null -eq $LASTEXITCODE) { 1 } else { $LASTEXITCODE }
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
    $env:QUOTA_GOOGLE_TOKEN_URL = 'http://127.0.0.1:1'
    $env:QUOTA_GOOGLE_USERINFO_URL = 'http://127.0.0.1:1'
    # 未注入替身时指向不存在的可执行文件，避免 PATH 上的真实 agy 被误触发。
    $env:QUOTA_GOOGLE_CLI = Join-Path $runDir 'agy-not-installed'
    $env:PYTHONDONTWRITEBYTECODE = '1'

    Write-Host ("Test run directory: {0}" -f $runDir)
    Write-Host ("Isolated QUOTA_STATE_FILE: {0}" -f $env:QUOTA_STATE_FILE)
    Write-Host 'Isolated service base URLs: Codex / DeepSeek / MiMo / Google -> http://127.0.0.1:1'

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
    foreach ($credential in @('QUOTA_CODEX_ACCESS_TOKEN', 'DEEPSEEK_API_KEY', 'MIMO_API_KEY', 'QUOTA_GOOGLE_CLIENT_ID', 'QUOTA_GOOGLE_CLIENT_SECRET')) {
        [Environment]::SetEnvironmentVariable($credential, $null, 'Process')
    }

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
