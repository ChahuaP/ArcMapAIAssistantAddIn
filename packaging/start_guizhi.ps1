param(
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$DesktopRuntime,
    [string]$DshRuntime,
    [string]$UserData,
    [string]$ReleaseRoot
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'build_paths.ps1')
$RuntimeRoot = Assert-ExternalBuildPath $RuntimeRoot $repo
$DesktopRuntime = Assert-ExternalBuildPath $DesktopRuntime $repo
$electron = Join-Path $DesktopRuntime 'node_modules/electron/dist/electron.exe'
if (-not (Test-Path -LiteralPath $electron -PathType Leaf)) { throw "Electron runtime not found: $electron" }
$flags = @('--runtime', $RuntimeRoot, '--renderer-runtime', $DesktopRuntime)
if ($DshRuntime) { $flags += '--dsh', (Assert-ExternalBuildPath $DshRuntime $repo) }
if ($UserData) { $flags += '--user-data', (Assert-ExternalBuildPath $UserData $repo) }
$source = $repo
if ($ReleaseRoot) {
    $registry = Assert-ExternalBuildPath $ReleaseRoot $repo
    $bundle = & (Join-Path $RuntimeRoot 'python/python.exe') -I -B (Join-Path $repo 'src/guizhi/__main__.py') --workspace $registry release-active $registry
    if ($LASTEXITCODE -ne 0) { throw 'Could not verify the active Guizhi release' }
    $source = ($bundle | ConvertFrom-Json).source
}
& $electron (Join-Path $source 'apps/desktop/entry.cjs') @flags | Out-Default
if ($LASTEXITCODE -ne 0) { throw "Guizhi exited with $LASTEXITCODE" }
