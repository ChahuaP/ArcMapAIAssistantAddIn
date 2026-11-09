param(
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$DesktopRuntime,
    [string]$DshRuntime,
    [string]$UserData
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
& $electron (Join-Path $repo 'apps/desktop/main.cjs') @flags
if ($LASTEXITCODE -ne 0) { throw "Guizhi exited with $LASTEXITCODE" }
