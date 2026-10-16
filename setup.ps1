<#
.SYNOPSIS
    One-time setup of the Claude Code <-> Houdini integration from this repository.

.DESCRIPTION
    Run once after "git clone". Safe to re-run: each step checks the current state
    and only changes what is missing or different.

      1. Python   : creates .venv in this repository and runs "pip install -e ." into it.
      2. Houdini  : for every Houdini found under "<Program Files>\Side Effects Software\Houdini <ver>",
                    writes Documents\houdini<major.minor>\packages\houdini_mcp.json, which points
                    Houdini at <repo>\houdini_plugin (auto-starts the RPyC server on port 18811).
      3. Claude   : registers this repository as a local plugin marketplace
                    ("claude plugin marketplace add <repo>") and installs the plugin from it at
                    user scope ("claude plugin install <plugin>@<marketplace> --scope user").
                    The plugin provides the houdini-mcp MCP server, the gate hooks, the skills,
                    and rules/HOUDINI_RULES.md. It is loaded in place from this repository.

    Every path is derived from this script's location, the Documents folder
    ([Environment]::GetFolderPath('MyDocuments')) and $env:ProgramFiles. HOME is never read,
    because Git Bash sets it to the profile folder, which is not where Houdini keeps its prefs.

    Use -WhatIf to print every action without performing it.

.PARAMETER Python
    Python 3.10+ interpreter used to create .venv. Default: the first of
    "py -3.12", "py -3.11", "py -3.10", "py -3.13", "python" that works.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1 -WhatIf
    powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Python
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Paths (all derived from this script's location and well-known folders)
# ---------------------------------------------------------------------------

function Resolve-RealPath([string]$Path) {
    # Resolve junctions/symlinks in every ancestor. Claude Code does not read a
    # local marketplace source that traverses a link, so register the real path.
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full.StartsWith('\\')) { return $full }
    $parts = $full.Split('\')
    $current = $parts[0] + '\'
    for ($i = 1; $i -lt $parts.Length; $i++) {
        $current = Join-Path $current $parts[$i]
        $item = Get-Item -LiteralPath $current -Force
        if ($item.LinkType -and $item.Target) {
            $target = @($item.Target)[0]
            if (-not [System.IO.Path]::IsPathRooted($target)) {
                $target = Join-Path (Split-Path $current -Parent) $target
            }
            $current = [System.IO.Path]::GetFullPath($target).TrimEnd('\')
        }
    }
    return $current
}

$RepoRoot      = Resolve-RealPath $PSScriptRoot
$VenvDir       = Join-Path $RepoRoot '.venv'
$VenvPython    = Join-Path $VenvDir 'Scripts\python.exe'
$PackageTpl    = Join-Path $RepoRoot 'houdini_plugin\houdini_mcp.json'
$HoudiniPlugin = (Join-Path $RepoRoot 'houdini_plugin') -replace '\\', '/'
$Documents     = [Environment]::GetFolderPath('MyDocuments')
$SesiRoot      = Join-Path $env:ProgramFiles 'Side Effects Software'

$Marketplace     = Get-Content -LiteralPath (Join-Path $RepoRoot '.claude-plugin\marketplace.json') -Raw | ConvertFrom-Json
$MarketplaceName = $Marketplace.name
$PluginName      = @($Marketplace.plugins)[0].name
$PluginId        = "$PluginName@$MarketplaceName"

function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Info([string]$Text) { Write-Host "   $Text" }

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

function Get-PathKey([string]$Path) {
    if (-not $Path) { return '' }
    return ([System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')).ToLowerInvariant()
}

Write-Host "houdini-mcp setup"
Write-Info "Repository : $RepoRoot"
Write-Info "Documents  : $Documents"
Write-Info "Plugin     : $PluginId"

# ---------------------------------------------------------------------------
# 1. Python virtual environment
# ---------------------------------------------------------------------------

Write-Step '1/3 Python environment (.venv)'

function Find-Python {
    $candidates = @()
    if ($Python) { $candidates += ,@($Python) }
    else {
        $candidates += ,@('py', '-3.12')
        $candidates += ,@('py', '-3.11')
        $candidates += ,@('py', '-3.10')
        $candidates += ,@('py', '-3.13')
        $candidates += ,@('python')
    }
    foreach ($cand in $candidates) {
        $exe = $cand[0]
        $pre = @($cand | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $out = & $exe @pre -c 'import sys; assert sys.version_info >= (3, 10); print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) { return ([string]@($out)[-1]).Trim() }
        } catch { }
    }
    return $null
}

if (Test-Path -LiteralPath $VenvPython) {
    Write-Info "Exists: $VenvPython"
} else {
    $basePython = Find-Python
    if (-not $basePython) { throw 'No Python 3.10+ found. Install Python 3.12 or pass -Python <path-to-python.exe>.' }
    Write-Info "Base Python: $basePython"
    if ($PSCmdlet.ShouldProcess($VenvDir, "Create virtual environment with $basePython")) {
        Invoke-Native $basePython @('-m', 'venv', $VenvDir)
        Write-Info "Created: $VenvDir"
    }
}

if ($PSCmdlet.ShouldProcess($VenvPython, "pip install -e $RepoRoot")) {
    Invoke-Native $VenvPython @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '-e', $RepoRoot)
    Write-Info 'Installed houdini-mcp (editable) into .venv'
}

# ---------------------------------------------------------------------------
# 2. Houdini packages
# ---------------------------------------------------------------------------

Write-Step '2/3 Houdini package files'

$versions = @()
if (Test-Path -LiteralPath $SesiRoot) {
    foreach ($dir in Get-ChildItem -LiteralPath $SesiRoot -Directory) {
        if ($dir.Name -match '^Houdini (\d+)\.(\d+)\.\d+') {
            $versions += "$($Matches[1]).$($Matches[2])"
        }
    }
}
$versions = @($versions | Sort-Object -Unique)

if ($versions.Count -eq 0) {
    Write-Warning "No Houdini installation found under $SesiRoot. Skipping Houdini packages."
} else {
    $template = Get-Content -LiteralPath $PackageTpl -Raw
    $pattern = '("HOUDINI_MCP_ROOT"\s*:\s*)"[^"]*"'
    if ($template -notmatch $pattern) { throw "HOUDINI_MCP_ROOT entry not found in $PackageTpl" }
    $content = [regex]::Replace($template, $pattern, { param($m) $m.Groups[1].Value + '"' + $HoudiniPlugin + '"' })
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    foreach ($ver in $versions) {
        $pkgDir  = Join-Path $Documents "houdini$ver\packages"
        $pkgFile = Join-Path $pkgDir 'houdini_mcp.json'
        $current = $null
        if (Test-Path -LiteralPath $pkgFile) { $current = Get-Content -LiteralPath $pkgFile -Raw }
        if ($current -and (($current -replace "`r`n", "`n").Trim() -eq ($content -replace "`r`n", "`n").Trim())) {
            Write-Info "Houdini $ver : up to date ($pkgFile)"
            continue
        }
        $action = 'Create'
        if ($current) { $action = 'Overwrite' }
        if ($PSCmdlet.ShouldProcess($pkgFile, "$action Houdini $ver package (HOUDINI_MCP_ROOT=$HoudiniPlugin)")) {
            if (-not (Test-Path -LiteralPath $pkgDir)) { New-Item -ItemType Directory -Path $pkgDir -Force | Out-Null }
            [System.IO.File]::WriteAllText($pkgFile, $content, $utf8NoBom)
            Write-Info "Houdini $ver : wrote $pkgFile"
        }
    }
}

# ---------------------------------------------------------------------------
# 3. Claude Code plugin
# ---------------------------------------------------------------------------

Write-Step '3/3 Claude Code plugin'

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    throw 'Claude Code CLI ("claude") not found on PATH. Install Claude Code, then re-run this script.'
}
Write-Info ("Claude Code: " + ((& claude --version) | Select-Object -First 1))

$registered = $null
$mpJson = (& claude plugin marketplace list --json) -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'claude plugin marketplace list failed' }
$mpList = $mpJson | ConvertFrom-Json   # PS 5.1 returns the array as one object; foreach enumerates it
foreach ($mp in $mpList) {
    if ($mp -and $mp.name -eq $MarketplaceName) { $registered = $mp }
}

$needAdd = $true
if ($registered) {
    $regPath = $null
    if ($registered.PSObject.Properties['path']) { $regPath = $registered.path }
    if ($registered.source -eq 'directory' -and (Get-PathKey $regPath) -eq (Get-PathKey $RepoRoot)) {
        Write-Info "Marketplace '$MarketplaceName' already registered: $regPath"
        $needAdd = $false
    } else {
        $where = $regPath
        if (-not $where) { $where = $registered.source }
        if ($PSCmdlet.ShouldProcess($MarketplaceName, "Remove marketplace registered from '$where' (re-added from $RepoRoot)")) {
            Invoke-Native 'claude' @('plugin', 'marketplace', 'remove', $MarketplaceName)
        }
    }
}
if ($needAdd -and $PSCmdlet.ShouldProcess($RepoRoot, 'claude plugin marketplace add (user scope)')) {
    Invoke-Native 'claude' @('plugin', 'marketplace', 'add', $RepoRoot, '--scope', 'user')
}

if ($PSCmdlet.ShouldProcess($PluginId, 'claude plugin install --scope user')) {
    Invoke-Native 'claude' @('plugin', 'install', $PluginId, '--scope', 'user')
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

Write-Step 'Done'
if ($WhatIfPreference) {
    Write-Info 'Dry run (-WhatIf): nothing was changed.'
} else {
    Write-Info 'Restart Claude Code (or run /reload-plugins) and restart Houdini to pick up the changes.'
}
