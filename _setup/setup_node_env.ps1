#Requires -Version 5.1
<#
.SYNOPSIS
    Node.js environment setup for Windows (PowerShell).

.DESCRIPTION
    Ensures npm dependencies are installed for a skill directory.
    Optionally switches to the Node version specified in .nvmrc / .node-version
    using nvm-windows or fnm when available.

.PARAMETER SkillDir
    Path to the skill directory. Defaults to the current directory.

.EXAMPLE
    .\setup_node_env.ps1 C:\path\to\skill
#>

param(
    [string]$SkillDir = (Get-Location).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Log($msg) { Write-Host "[setup_node_env] $msg" }
function Err($msg) { Write-Error "[setup_node_env] ERROR: $msg"; exit 1 }

# ── 1. Ensure node is available ──────────────────────────────────────────────
$nodePath = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodePath) {
    # Try nvm-windows
    $nvmExe = "$env:APPDATA\nvm\nvm.exe"
    if (Test-Path $nvmExe) {
        Log "nvm-windows found. Attempting: nvm use latest …"
        & $nvmExe use latest 2>&1 | Out-Null
    }
    $nodePath = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodePath) {
        Err "node is not installed. Install Node.js from https://nodejs.org or via nvm-windows / fnm."
    }
}

$nodeVersion = & node --version
Log "node $nodeVersion found at $($nodePath.Source)"

# ── 2. Switch Node version if .nvmrc / .node-version is present ──────────────
$versionFile = $null
if (Test-Path (Join-Path $SkillDir ".nvmrc")) {
    $versionFile = Join-Path $SkillDir ".nvmrc"
} elseif (Test-Path (Join-Path $SkillDir ".node-version")) {
    $versionFile = Join-Path $SkillDir ".node-version"
}

if ($versionFile) {
    $requiredVersion = (Get-Content $versionFile -Raw).Trim()
    Log "Required Node version: $requiredVersion"

    $nvmExe = "$env:APPDATA\nvm\nvm.exe"
    $fnmExe = Get-Command fnm -ErrorAction SilentlyContinue

    if (Test-Path $nvmExe) {
        Log "Switching via nvm-windows …"
        & $nvmExe install $requiredVersion 2>&1 | Out-Null
        & $nvmExe use $requiredVersion
        Log "Using node $(& node --version) via nvm-windows"
    } elseif ($fnmExe) {
        Log "Switching via fnm …"
        & fnm install $requiredVersion 2>&1 | Out-Null
        & fnm use $requiredVersion
        Log "Using node $(& node --version) via fnm"
    } else {
        Log "No version manager found; using system node $nodeVersion (required: $requiredVersion)"
    }
}

# ── 3. Install npm dependencies ───────────────────────────────────────────────
$packageJson = Join-Path $SkillDir "package.json"
if (Test-Path $packageJson) {
    Log "Running npm install in $SkillDir …"
    Push-Location $SkillDir
    try {
        & npm install
        if ($LASTEXITCODE -ne 0) { Err "npm install failed with exit code $LASTEXITCODE" }
        Log "npm install complete."
    } finally {
        Pop-Location
    }
} else {
    Log "No package.json found in $SkillDir — skipping npm install."
}

Log "Done."
