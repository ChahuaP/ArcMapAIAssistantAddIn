param(
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$CacheRoot,
    [ValidateSet('asr','tts','both')][string]$Mode = 'both',
    [string]$BuildPython = 'python'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'build_paths.ps1')
$repo = Split-Path $PSScriptRoot -Parent
$root = Assert-ExternalBuildPath $RuntimeRoot $repo
$cache = Assert-ExternalBuildPath $CacheRoot $repo
if (Test-Path -LiteralPath $root) { throw 'Local speech runtime destination must be new' }
if ($root -eq $cache -or $root.StartsWith($cache + '\', 'OrdinalIgnoreCase') -or $cache.StartsWith($root + '\', 'OrdinalIgnoreCase')) {
    throw 'Local speech runtime and cache must be separate trees'
}
$lock = Join-Path $PSScriptRoot 'local-speech/requirements.lock'
$spec = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'local-speech/models.lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$wheelCache = Join-Path $cache 'wheels'
[IO.Directory]::CreateDirectory($wheelCache) | Out-Null
& $BuildPython -B -m pip --isolated download --disable-pip-version-check --only-binary=:all: --require-hashes -r $lock --dest $wheelCache
if ($LASTEXITCODE -ne 0) { throw 'Local speech wheels could not be verified' }
$engine = Join-Path $root 'engine'
[IO.Directory]::CreateDirectory($engine) | Out-Null
& $BuildPython -B -m pip --isolated install --disable-pip-version-check --no-index --find-links $wheelCache --only-binary=:all: --require-hashes --no-compile --ignore-installed --target $engine -r $lock
if ($LASTEXITCODE -ne 0) { throw 'Local speech engine installation failed' }
$installed = @()
foreach ($kind in 'asr','tts') {
    if ($Mode -ne 'both' -and $Mode -ne $kind) { continue }
    $model = $spec.models.$kind
    foreach ($item in $model.files) {
        $store = Join-Path $cache ('asset-' + $item.sha256)
        if (-not (Test-Path -LiteralPath $store)) {
            $pending = $store + '.download'
            Invoke-WebRequest -Uri ($model.base_url + $item.file) -TimeoutSec 150 -OutFile $pending
            if ((Get-FileHash -LiteralPath $pending).Hash.ToLowerInvariant() -ne $item.sha256) { throw "Model checksum mismatch: $kind/$($item.file)" }
            [IO.File]::Move($pending, $store)
        }
        if ((Get-FileHash -LiteralPath $store).Hash.ToLowerInvariant() -ne $item.sha256) { throw "Cached model checksum mismatch: $kind/$($item.file)" }
        $target = Join-Path (Join-Path $root "models/$kind") $item.file
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
        Copy-Item -LiteralPath $store -Destination $target
    }
    $installed += $kind
}
@{engine=$spec.engine; modes=$installed} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'identity.json') -Encoding UTF8
Write-Output "local speech: $root"
