param(
    [switch]$Clean,
    [switch]$OneFile = $true,
    [switch]$EmbedApiKey,
    [string]$NativeBuildDirectory,
    [switch]$RequireExperimentalHook
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing project venv: $python" }

& $python -m pip install --disable-pip-version-check "pyinstaller>=6.16,<7"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed" }

$args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--name", "JevDefaultOpen",
    "--paths", (Join-Path $root "src"),
    "--collect-all", "jev_open",
    (Join-Path $root "packaged_entry.py")
)
if ($OneFile) { $args += "--onefile" }
$nativeDir = if ($NativeBuildDirectory) {
    [IO.Path]::GetFullPath($NativeBuildDirectory)
} else {
    Join-Path $root "native\build\Release"
}
foreach ($nativeName in @("native_host.exe", "open_hook.dll", "native_claim.dll")) {
    $nativePath = Join-Path $nativeDir $nativeName
    if (-not (Test-Path $nativePath)) { throw "Missing native artifact: $nativePath" }
    $args += @("--add-binary", "$nativePath;.")
}
if ($RequireExperimentalHook) {
    $capability = & (Join-Path $nativeDir "native_host.exe") --self-test | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $capability.experimental_hook_enabled -ne $true) {
        throw "Selected native artifacts do not enable the experimental hook"
    }
    $probePath = Join-Path $nativeDir "hook_capture_probe.exe"
    if (-not (Test-Path $probePath)) { throw "Missing Hook capture probe: $probePath" }
    $args += @("--add-binary", "$probePath;.")
}
if ($EmbedApiKey) {
    if (-not $env:TYPESAFE_API_KEY) {
        throw "Set TYPESAFE_API_KEY in the current process before using -EmbedApiKey"
    }
    # Explicit opt-in. The resulting EXE contains a recoverable copy of the key.
    $keyPath = Join-Path $root "build\jev_api_key.txt"
    [System.IO.File]::WriteAllText($keyPath, $env:TYPESAFE_API_KEY, [Text.UTF8Encoding]::new($false))
    $args += @("--add-data", "$keyPath;.")
}
$originalPath = $env:PATH
try {
    # Qt6Core imports the Windows system ICU shim. A foreign Poppler ICU on
    # PATH makes PyInstaller bundle an ABI-incompatible icuuc.dll beside Qt.
    $env:PATH = (($originalPath -split ";") | Where-Object {
        $_ -notmatch "codex-primary-runtime|[\\/]poppler[\\/]"
    }) -join ";"
    & $python @args
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
} finally {
    $env:PATH = $originalPath
}
if ($EmbedApiKey -and (Test-Path $keyPath)) { Remove-Item -LiteralPath $keyPath -Force }

$builtExe = if ($OneFile) {
    Join-Path $root "dist\JevDefaultOpen.exe"
} else {
    Join-Path $root "dist\JevDefaultOpen\JevDefaultOpen.exe"
}
& $builtExe --qt-smoke
if ($LASTEXITCODE -ne 0) { throw "Bundled QtCore smoke test failed" }
if ($RequireExperimentalHook) {
    & $builtExe --hook-smoke
    if ($LASTEXITCODE -ne 0) { throw "Bundled Hook end-to-end smoke test failed" }
}

Write-Host "Built: $builtExe"
Write-Host "Live mode prompts for TYPESAFE_API_KEY at startup. Use --demo for offline demo."
