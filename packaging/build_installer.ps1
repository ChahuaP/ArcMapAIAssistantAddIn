param(
    [Parameter(Mandatory)][string]$BuildRoot,
    [Parameter(Mandatory)][string]$CacheRoot,
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$CompilerPath,
    [switch]$SkipHarnessBuild
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'build_paths.ps1')
$BuildRoot = Assert-ExternalBuildPath $BuildRoot $repo
$CacheRoot = Assert-ExternalBuildPath $CacheRoot $repo
$release = Assert-ExternalBuildPath $ReleaseRoot $repo
$stage = Join-Path $BuildRoot 'harness-staging'
$iss = Join-Path $PSScriptRoot 'ArcMapHarnessSetup.iss'

if (-not $SkipHarnessBuild) {
    & (Join-Path $PSScriptRoot 'build_harness.ps1') -BuildRoot $BuildRoot -CacheRoot $CacheRoot
    if ($LASTEXITCODE -ne 0) { throw 'build_harness.ps1 failed' }
}
if (-not (Test-Path (Join-Path $stage 'harness_identity.json'))) {
    throw "staging 缺少 harness_identity.json：$stage"
}

if (-not [IO.Path]::IsPathFullyQualified($CompilerPath) -or -not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) {
    throw 'CompilerPath must name an existing absolute Inno Setup 7.1.1 x64 ISCC.exe path'
}
$iscc = $CompilerPath
$compilerLock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installer.lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $iscc).Hash.ToLowerInvariant() -ne $compilerLock.compiler_sha256) {
    throw 'Inno Setup compiler does not match installer.lock.json'
}

New-Item -ItemType Directory -Force -Path $release | Out-Null
$version = (Get-Content -LiteralPath (Join-Path $repo 'VERSION') -Raw -Encoding UTF8).Trim()
if (Test-Path -LiteralPath (Join-Path $release "ArcMapHarnessSetup-$version.exe")) { throw 'Refusing to overwrite an existing installer; choose a new release build directory' }
& $iscc /Qp "/DMySourceDir=$stage" "/DMyOutputDir=$release" "/DMyAppVersion=$version" $iss
if ($LASTEXITCODE -ne 0) { throw "ISCC 编译失败，退出码：$LASTEXITCODE" }

$version = (Get-Content (Join-Path $repo 'VERSION') -Raw).Trim()
$exe = Join-Path $release "ArcMapHarnessSetup-$version.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "安装包未生成：$exe" }
Write-Output ("installer: {0}" -f $exe)
