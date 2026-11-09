# Prepare the pinned Electron runtime in an external build directory.
param(
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$CacheRoot
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'build_paths.ps1')
$repo = Split-Path $PSScriptRoot -Parent
$RuntimeRoot = Assert-ExternalBuildPath $RuntimeRoot $repo
$CacheRoot = Assert-ExternalBuildPath $CacheRoot $repo
if (Test-Path -LiteralPath $RuntimeRoot) { throw "Desktop runtime destination must be new: $RuntimeRoot" }
[IO.Directory]::CreateDirectory($RuntimeRoot) | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'desktop-runtime\package.json'), (Join-Path $PSScriptRoot 'desktop-runtime\package-lock.json') -Destination $RuntimeRoot
$before = @{}
foreach ($key in 'npm_config_cache', 'electron_config_cache') { $before[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
try {
    $env:npm_config_cache = Join-Path $CacheRoot 'npm'
    $env:electron_config_cache = Join-Path $CacheRoot 'electron'
    & npm ci --prefix $RuntimeRoot --ignore-scripts --engine-strict --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Locked Electron dependency installation failed' }
    & node (Join-Path $RuntimeRoot 'node_modules\electron\install.js')
    if ($LASTEXITCODE -ne 0) { throw 'Electron artifact download failed' }
    $binary = Join-Path $RuntimeRoot 'node_modules\electron\dist\electron.exe'
    if (-not (Test-Path -LiteralPath $binary)) { throw 'Electron binary missing' }
    $avatar = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'desktop-runtime\avatar.lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $assets = Join-Path $RuntimeRoot 'assets'
    [IO.Directory]::CreateDirectory($assets) | Out-Null
    $cachedAvatar = Join-Path $CacheRoot ('avatar-' + $avatar.sha256 + '.vrm')
    if (-not (Test-Path -LiteralPath $cachedAvatar)) {
        $download = $cachedAvatar + '.download'
        Invoke-WebRequest -Uri $avatar.url -TimeoutSec 90 -OutFile $download
        if ((Get-FileHash -LiteralPath $download).Hash.ToLowerInvariant() -ne $avatar.sha256) { throw 'Avatar checksum mismatch' }
        [IO.File]::Move($download, $cachedAvatar)
    }
    if ((Get-FileHash -LiteralPath $cachedAvatar).Hash.ToLowerInvariant() -ne $avatar.sha256) { throw 'Cached avatar checksum mismatch' }
    Copy-Item -LiteralPath $cachedAvatar -Destination (Join-Path $assets 'avatar.vrm')
    $manifest = [ordered]@{
        electron = (Get-Content -LiteralPath (Join-Path $RuntimeRoot 'node_modules\electron\package.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
        binary_sha256 = (Get-FileHash -LiteralPath $binary).Hash.ToLowerInvariant()
        package_lock_sha256 = (Get-FileHash -LiteralPath (Join-Path $RuntimeRoot 'package-lock.json')).Hash.ToLowerInvariant()
        avatar_sha256 = $avatar.sha256
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RuntimeRoot 'runtime-identity.json') -Encoding UTF8
    Write-Output $binary
} finally {
    foreach ($key in $before.Keys) { [Environment]::SetEnvironmentVariable($key, $before[$key], 'Process') }
}
$global:LASTEXITCODE = 0
