[CmdletBinding()]
param(
    [string]$ZigPath,
    [string]$BuildDirectory,
    [switch]$EnableExperimentalHook,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$native = Join-Path $root 'native'
$build = if ($BuildDirectory) {
    [System.IO.Path]::GetFullPath($BuildDirectory)
} else {
    Join-Path $native 'build'
}

$localCmake = Join-Path $root '.venv\Scripts\cmake.exe'
$cmake = Get-Command cmake -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $localCmake) {
    $cmakePath = $localCmake
} elseif ($cmake) {
    $cmakePath = $cmake.Source
} else {
    throw 'CMake is unavailable. Install it explicitly or use bootstrap -InstallToolchain.'
}

$configure = @('-S', $native, '-B', $build, '-DBUILD_TESTING=ON')
if ($ZigPath) {
    if ($EnableExperimentalHook) {
        throw 'The experimental Detours hook requires MSVC; Zig builds the safe test transport only.'
    }
    if (-not (Test-Path -LiteralPath $ZigPath -PathType Leaf)) {
        throw "Zig executable is unavailable: $ZigPath"
    }
    $ninja = Join-Path $root '.venv\Scripts\ninja.exe'
    if (-not (Test-Path -LiteralPath $ninja)) {
        $ninjaCommand = Get-Command ninja -ErrorAction SilentlyContinue
        if (-not $ninjaCommand) { throw 'Ninja is required for the optional Zig build.' }
        $ninja = $ninjaCommand.Source
    }
    $configure += @(
        '-G', 'Ninja',
        "-DCMAKE_CXX_COMPILER=$ZigPath",
        '-DCMAKE_CXX_COMPILER_ARG1=c++',
        "-DCMAKE_MAKE_PROGRAM=$ninja",
        '-DCMAKE_BUILD_TYPE=Release'
    )
} else {
    if ($EnableExperimentalHook) {
        # CMake's Visual Studio generator can fail much later with a vague
        # compiler error.  Fail before configure unless this is an x64
        # developer shell with the tools needed by the Detours branch.
        $cl = Get-Command cl.exe -ErrorAction SilentlyContinue
        $link = Get-Command link.exe -ErrorAction SilentlyContinue
        if (-not $cl -or -not $link) {
            throw 'The experimental Detours hook requires an MSVC x64 developer shell (cl.exe and link.exe). Run from "x64 Native Tools Command Prompt for VS" or use the default OFF build.'
        }
        if ($env:VSCMD_ARG_TGT_ARCH -and $env:VSCMD_ARG_TGT_ARCH -ne 'x64') {
            throw "The experimental Detours hook requires an x64 MSVC environment; VSCMD_ARG_TGT_ARCH is '$($env:VSCMD_ARG_TGT_ARCH)'."
        }
    }
    $configure += @('-A', 'x64')
}
$hookOption = if ($EnableExperimentalHook) { 'ON' } else { 'OFF' }
$configure += "-DJEV_ENABLE_EXPERIMENTAL_HOOK=$hookOption"

$jsonHeader = Join-Path $build '_deps\json\nlohmann\json.hpp'
$jsonHash = '9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6'
$jsonReady = (Test-Path -LiteralPath $jsonHeader) -and (
    (Get-FileHash -LiteralPath $jsonHeader -Algorithm SHA256).Hash -eq $jsonHash
)
if (-not $jsonReady) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $jsonHeader) | Out-Null
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/nlohmann/json/v3.11.3/single_include/nlohmann/json.hpp' -OutFile $jsonHeader -TimeoutSec 60
    if ((Get-FileHash -LiteralPath $jsonHeader -Algorithm SHA256).Hash -ne $jsonHash) {
        throw 'Pinned JSON header failed SHA256 verification.'
    }
}

& $cmakePath @configure
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed ($LASTEXITCODE)." }
& $cmakePath --build $build --config Release --parallel 4
if ($LASTEXITCODE -ne 0) { throw "Native compilation failed ($LASTEXITCODE)." }

foreach ($name in @('native_host.exe', 'open_hook.dll', 'native_claim.dll')) {
    $artifact = Join-Path $build "Release\$name"
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Required native build artifact is missing: $artifact"
    }
    Write-Host $artifact
}

if (-not $SkipTests) {
    $ctest = Join-Path (Split-Path -Parent $cmakePath) 'ctest.exe'
    if (-not (Test-Path -LiteralPath $ctest)) {
        throw 'CTest is unavailable beside CMake.'
    }
    & $ctest --test-dir $build -C Release --output-on-failure --no-tests=error
    if ($LASTEXITCODE -ne 0) { throw "Native protocol tests failed ($LASTEXITCODE)." }
}
Write-Host "Experimental desktop interception compiled: $hookOption"
