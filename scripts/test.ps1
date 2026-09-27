#Requires -Version 5.1
<#
.SYNOPSIS
Run offline lint and Python tests without opening desktop windows.
.DESCRIPTION
Uses the project's existing virtual environment. No packages are installed and
no global hooks or live model requests are initiated by this script.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Virtual environment not found. Run scripts\bootstrap.ps1 first.'
}

$oldQtPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = 'offscreen'
Push-Location -LiteralPath $root
try {
    & $python -m ruff check .
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Ruff failed with exit code $exitCode."
    }
    & $python -m pytest
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Pytest failed with exit code $exitCode."
    }
} finally {
    Pop-Location
    $env:QT_QPA_PLATFORM = $oldQtPlatform
}
