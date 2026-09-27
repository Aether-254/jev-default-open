#Requires -Version 5.1
<#
.SYNOPSIS
Start Jev Default Open using the existing project-local virtual environment.
.DESCRIPTION
This script does not install dependencies, request elevation, enable hooks,
or read/print API credentials. The application owns onboarding and permissions.
An optional -Config path is passed to the application's --config option.
#>
[CmdletBinding()]
param([string]$Config)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Virtual environment not found. Run scripts\bootstrap.ps1 first.'
}
$arguments = @((Join-Path $root 'main.py'))
if (-not [string]::IsNullOrWhiteSpace($Config)) {
    if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
        throw 'The -Config path must name an existing configuration file.'
    }
    $arguments += @('--config', (Resolve-Path -LiteralPath $Config).ProviderPath)
}

Push-Location -LiteralPath $root
try {
    & $python @arguments
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $exitCode
