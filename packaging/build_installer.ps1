# Build the one-click ArcMap Harness installer:
#   pwsh -NoProfile -File packaging\build_installer.ps1
# Steps: build_harness.ps1 -> stage; Inno Setup (ISCC) -> release\ArcMapHarnessSetup-<version>.exe
param(
    [switch]$SkipHarnessBuild
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$stage = Join-Path $repo 'build\harness-staging'
$release = Join-Path $repo 'release'
$iss = Join-Path $PSScriptRoot 'ArcMapHarnessSetup.iss'

if (-not $SkipHarnessBuild) {
    & (Join-Path $PSScriptRoot 'build_harness.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'build_harness.ps1 failed' }
}
if (-not (Test-Path (Join-Path $stage 'harness_identity.json'))) {
    throw "staging 缺少 harness_identity.json：$stage"
}

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $iscc = $candidate; break }
    }
}
if (-not $iscc) {
    throw "找不到 ISCC.exe（Inno Setup 6）。请先安装：winget install --id JRSoftware.InnoSetup"
}

New-Item -ItemType Directory -Force -Path $release | Out-Null
& $iscc "/DMySourceDir=$stage" "/DMyOutputDir=$release" $iss
if ($LASTEXITCODE -ne 0) { throw "ISCC 编译失败，退出码：$LASTEXITCODE" }

$version = (Get-Content (Join-Path $repo 'VERSION') -Raw).Trim()
$exe = Join-Path $release "ArcMapHarnessSetup-$version.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "安装包未生成：$exe" }
Write-Output ("installer: {0}" -f $exe)
