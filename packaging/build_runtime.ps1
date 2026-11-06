# Build a pinned Windows x64 runtime outside the source checkout.
param(
    [Parameter(Mandatory)][string]$Stage,
    [Parameter(Mandatory)][string]$CacheRoot,
    [string]$BuildPython = 'python'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'build_paths.ps1')
$repo = Split-Path $PSScriptRoot -Parent
$Stage = Assert-ExternalBuildPath $Stage $repo
$CacheRoot = Assert-ExternalBuildPath $CacheRoot $repo
if ($Stage -eq $CacheRoot -or $Stage.StartsWith($CacheRoot + '\', 'OrdinalIgnoreCase') -or $CacheRoot.StartsWith($Stage + '\', 'OrdinalIgnoreCase')) {
    throw 'Stage and runtime cache must be separate trees'
}
[IO.Directory]::CreateDirectory($CacheRoot) | Out-Null
$spec = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$actualPython = & $BuildPython -B -c 'import platform; print(platform.python_version())'
if ($LASTEXITCODE -ne 0 -or $actualPython.Trim() -ne $spec.python.version) { throw "Build Python must be $($spec.python.version)" }

function Get-LockedDownload($Entry, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Destination)) {
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Destination)) | Out-Null
        $partial = $Destination + '.download'
        Invoke-WebRequest -Uri $Entry.url -OutFile $partial
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Entry.sha256) {
            throw "Downloaded artifact checksum mismatch: $Destination"
        }
        [IO.File]::Move($partial, $Destination)
    }
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Entry.sha256) {
        throw "Cached artifact checksum mismatch: $Destination"
    }
    return $Destination
}

$node = Get-LockedDownload $spec.node (Join-Path $CacheRoot "node-$($spec.node.version)\node.exe")
$nodeVersion = & $node --version
if ($LASTEXITCODE -ne 0 -or $nodeVersion.Trim() -ne "v$($spec.node.version)") { throw 'Node version mismatch' }
$pythonZip = Get-LockedDownload $spec.python (Join-Path $CacheRoot "downloads\python-$($spec.python.version)-embed-amd64.zip")
$lock = Join-Path $repo 'requirements.lock'
$lockHash = (Get-FileHash -LiteralPath $lock).Hash.ToLowerInvariant()
$wheels = Join-Path $CacheRoot "wheels-$lockHash"
[IO.Directory]::CreateDirectory($wheels) | Out-Null
& $BuildPython -B -m pip --isolated download --disable-pip-version-check --only-binary=:all: --require-hashes --dest $wheels -r $lock
if ($LASTEXITCODE -ne 0) { throw 'Locked wheel download failed' }

$runtime = Join-Path $Stage 'harness\runtime'
$pythonDir = Join-Path $runtime 'python'
if (Test-Path -LiteralPath $runtime) { throw "Runtime stage must be new: $runtime" }
[IO.Directory]::CreateDirectory($pythonDir) | Out-Null
Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonDir
$site = Join-Path $pythonDir 'Lib\site-packages'
& $BuildPython -B -m pip --isolated install --disable-pip-version-check --ignore-installed --no-index --find-links $wheels --only-binary=:all: --require-hashes --no-compile --target $site -r $lock
if ($LASTEXITCODE -ne 0) { throw 'Locked Python runtime installation failed' }
@('python311.zip', '.', 'Lib\site-packages', 'import site') | Set-Content -LiteralPath (Join-Path $pythonDir 'python311._pth') -Encoding ASCII
$pythonExe = Join-Path $pythonDir 'python.exe'
& $pythonExe -I -B -c 'import sys; assert sys.flags.no_user_site; import fastmcp,pydantic,jsonschema,geopandas,shapely,pyproj,pyogrio,rasterio,win32api; print("Bundled Python and GIS imports passed")'
if ($LASTEXITCODE -ne 0) { throw 'Bundled Python import check failed' }

$nodeDir = Join-Path $runtime 'node'
[IO.Directory]::CreateDirectory($nodeDir) | Out-Null
Copy-Item -LiteralPath $node -Destination (Join-Path $nodeDir 'node.exe')
$dsh = Join-Path $runtime 'dsh'
[IO.Directory]::CreateDirectory($dsh) | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'dsh-runtime\package.json'), (Join-Path $PSScriptRoot 'dsh-runtime\package-lock.json') -Destination $dsh
$previousPath = $env:PATH
$previousCache = $env:npm_config_cache
try {
    $env:PATH = $nodeDir + ';' + $previousPath
    $env:npm_config_cache = Join-Path $CacheRoot 'npm'
    & npm ci --prefix $dsh --engine-strict --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Locked DSH installation failed' }
    & $node (Join-Path $dsh 'node_modules\@deepseek-ai\dsh\lib\bin.js') --version
    if ($LASTEXITCODE -ne 0) { throw 'Bundled DSH cannot start' }
} finally { $env:PATH = $previousPath; $env:npm_config_cache = $previousCache }
Copy-Item -LiteralPath $lock, (Join-Path $PSScriptRoot 'runtime.lock.json') -Destination $runtime
Write-Output "runtime: $runtime"
$global:LASTEXITCODE = 0
