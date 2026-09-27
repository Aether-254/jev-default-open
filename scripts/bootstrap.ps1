#Requires -Version 5.1
<#
.SYNOPSIS
Prepare the project-local Python environment and run its checks.
.DESCRIPTION
The default operation does not install system software or enable hooks.
-InstallToolchain explicitly permits winget to install missing Visual Studio
2022 C++ Build Tools and CMake. Those installers may require administrator
approval and download several gigabytes. Package/source agreements are accepted
only when this switch is supplied. -BuildNative separately requests a build.
.EXAMPLE
.\scripts\bootstrap.ps1
.EXAMPLE
.\scripts\bootstrap.ps1 -Python C:\Python312\python.exe -SkipChecks
.EXAMPLE
.\scripts\bootstrap.ps1 -InstallToolchain -BuildNative
#>
[CmdletBinding()]
param(
    [string]$Python,
    [switch]$InstallToolchain,
    [switch]$BuildNative,
    [switch]$SkipChecks
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    & $FilePath @ArgumentList
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$(Split-Path -Leaf $FilePath) failed with exit code $exitCode."
    }
}

function Install-MissingNativeToolchain {
    Write-Host 'Explicit toolchain installation requested. System installers may request UAC approval.'
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $hasCppTools = $false
    if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
        $installation = & $vswhere -latest -products '*' `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "vswhere failed with exit code $exitCode."
        }
        $hasCppTools = -not [string]::IsNullOrWhiteSpace(($installation -join ''))
    }
    $hasCMake = [bool](Get-Command cmake -CommandType Application -ErrorAction SilentlyContinue)
    $hasCMake = $hasCMake -or (Test-Path -LiteralPath 'C:\Program Files\CMake\bin\cmake.exe' -PathType Leaf)
    if ($hasCppTools -and $hasCMake) {
        Write-Host 'The C++ toolchain and CMake are already present.'
        return
    }
    $winget = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'winget is unavailable. Install the missing native toolchain manually, then rerun without -InstallToolchain.'
    }
    $common = @('--exact', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements')
    if (-not $hasCppTools) {
        Invoke-CheckedCommand $winget.Source (@('install', '--id', 'Microsoft.VisualStudio.2022.BuildTools') + $common + @(
            '--override', '--wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
        ))
    }
    if (-not $hasCMake) {
        Invoke-CheckedCommand $winget.Source (@('install', '--id', 'Kitware.CMake') + $common)
    }
}

try {
    if ($InstallToolchain) {
        Install-MissingNativeToolchain
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if ([string]::IsNullOrWhiteSpace($Python)) {
            $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
            if (-not $launcher) {
                throw 'Python 3.12 is required. Install it manually or pass -Python with its executable path.'
            }
            $detectedPython = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)'
            $exitCode = $LASTEXITCODE
            if ($exitCode -ne 0 -or -not $detectedPython) {
                throw 'The Python launcher could not find Python 3.12. Pass -Python with its executable path.'
            }
            $Python = ($detectedPython -join '').Trim()
        }
        Invoke-CheckedCommand $Python @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)')
        Invoke-CheckedCommand $Python @('-m', 'venv', (Join-Path $root '.venv'))
    }
    Invoke-CheckedCommand $venvPython @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)')
    Invoke-CheckedCommand $venvPython @('-m', 'pip', 'install', '-e', "${root}[dev]")
    if ($BuildNative) {
        $global:LASTEXITCODE = 0
        & (Join-Path $PSScriptRoot 'build-native.ps1')
        $succeeded = $?
        $exitCode = $LASTEXITCODE
        if (-not $succeeded -or $exitCode -ne 0) {
            throw "Native build script failed with exit code $exitCode."
        }
    }
    if (-not $SkipChecks) {
        $global:LASTEXITCODE = 0
        & (Join-Path $PSScriptRoot 'test.ps1')
        $succeeded = $?
        $exitCode = $LASTEXITCODE
        if (-not $succeeded -or $exitCode -ne 0) {
            throw "Project checks failed with exit code $exitCode."
        }
    }
    Write-Host 'Project environment is ready. No application, hook, or IM adapter was started.'
} catch {
    Write-Error -ErrorRecord $_ -ErrorAction Continue
    exit 1
}
