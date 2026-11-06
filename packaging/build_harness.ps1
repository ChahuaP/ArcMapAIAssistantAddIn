# Build the current runnable baseline. Independent Guizhi application replaces
# this composition in the later architecture rounds.
param(
    [Parameter(Mandatory)][string]$BuildRoot,
    [Parameter(Mandatory)][string]$CacheRoot,
    [string]$BuildPython = 'python'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'build_paths.ps1')
$repo = Split-Path $PSScriptRoot -Parent
$BuildRoot = Assert-ExternalBuildPath $BuildRoot $repo
$CacheRoot = Assert-ExternalBuildPath $CacheRoot $repo
$stage = Join-Path $BuildRoot 'harness-staging'
$harness = Join-Path $stage 'harness'
if (Test-Path -LiteralPath $stage) { throw "Use a new build root; stage already exists: $stage" }
[IO.Directory]::CreateDirectory($harness) | Out-Null
foreach ($directory in 'server', 'shared_runtime', 'operation_catalog', 'arcmap_runtime_py2', 'dsh') {
    Copy-BuildTree (Join-Path $repo $directory) (Join-Path $harness $directory) @('/XD', '__pycache__', 'node_modules', '/XF', '*.pyc', '*.pyo')
}
Copy-Item -LiteralPath (Join-Path $repo 'VERSION') -Destination $harness

$bridgeBuild = Join-Path $BuildRoot 'bridge'
& (Join-Path $repo 'ArcMapBridgeExternal\build.ps1') -BuildRoot $bridgeBuild
if ($LASTEXITCODE -ne 0) { throw 'Bridge build failed' }
Copy-BuildTree (Join-Path $bridgeBuild 'bin\Release') (Join-Path $harness 'bridge') @('/XF', '*.xml', '*.pdb')
Copy-Item -LiteralPath (Join-Path $repo 'arcmap_runtime_py2\deployment_identity.json') -Destination (Join-Path $harness 'bridge\deployment_identity.json')
foreach ($file in 'OpenAssistantWeb.cmd', 'launch_harness.ps1', 'configure_ollama.ps1') {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "harness\$file") -Destination $harness
}
& (Join-Path $PSScriptRoot 'build_runtime.ps1') -Stage $stage -CacheRoot $CacheRoot -BuildPython $BuildPython
if ($LASTEXITCODE -ne 0) { throw 'Runtime build failed' }
$python = Join-Path $harness 'runtime\python\python.exe'
& $python -I -B (Join-Path $repo 'ArcMapAIAssistantAddIn\makeaddin.py') --output (Join-Path $stage 'ArcMapAIAssistantAddIn.esriaddin')
if ($LASTEXITCODE -ne 0) { throw 'Add-in packaging failed' }

$version = (Get-Content -LiteralPath (Join-Path $repo 'VERSION') -Raw -Encoding UTF8).Trim()
$files = Get-ChildItem -LiteralPath $stage -Recurse -File | Sort-Object FullName
$entries = foreach ($file in $files) {
    [ordered]@{ path = [IO.Path]::GetRelativePath($stage, $file.FullName).Replace('\', '/'); sha256 = (Get-FileHash -LiteralPath $file.FullName).Hash.ToLowerInvariant() }
}
$identity = [ordered]@{
    harness_version = $version
    built_at = [DateTime]::UtcNow.ToString('o')
    source_commit = (& git -C $repo rev-parse HEAD)
    source_index_tree = (& git -C $repo write-tree)
    source_dirty = [bool](& git -C $repo status --porcelain)
    runtime_lock_sha256 = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'runtime.lock.json')).Hash.ToLowerInvariant()
    python_lock_sha256 = (Get-FileHash -LiteralPath (Join-Path $repo 'requirements.lock')).Hash.ToLowerInvariant()
    file_count = $files.Count
    files = @($entries)
}
$canonical = $entries | ConvertTo-Json -Depth 4 -Compress
$identity.deployment_hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($canonical))).ToLowerInvariant()
$identity | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $stage 'harness_identity.json') -Encoding UTF8
Write-Output "built: $stage v$version ($($files.Count) files)"
$global:LASTEXITCODE = 0
