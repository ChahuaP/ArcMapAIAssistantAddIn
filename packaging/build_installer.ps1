
param(
    [Parameter(Mandatory)][string]$StageRoot,
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$DesktopRuntime,
    [Parameter(Mandatory)][string]$DshRuntime,
    [Parameter(Mandatory)][string]$SourceTree,
    [string]$SourceCommit,
    [string]$SpeechRuntime,
    [string]$ArcmapRuntime,
    [string]$ArcmapAddin,
    [string]$ReleaseRoot,
    [string]$CompilerPath,
    [string]$BuildPython = 'python',
    [switch]$StageOnly
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'build_paths.ps1')
$repo = Split-Path $PSScriptRoot -Parent
$stage = Assert-ExternalBuildPath $StageRoot $repo
$arguments = @('--stage', $stage, '--runtime', (Assert-ExternalBuildPath $RuntimeRoot $repo),
    '--desktop', (Assert-ExternalBuildPath $DesktopRuntime $repo),
    '--dsh', (Assert-ExternalBuildPath $DshRuntime $repo), '--tree', $SourceTree)
if ($SourceCommit) { $arguments += @('--commit', $SourceCommit) }
if ($SpeechRuntime) { $arguments += @('--speech', (Assert-ExternalBuildPath $SpeechRuntime $repo)) }
if ($ArcmapRuntime) { $arguments += @('--arcmap', (Assert-ExternalBuildPath $ArcmapRuntime $repo)) }
if ($ArcmapAddin) { $arguments += @('--addin', (Assert-ExternalBuildPath $ArcmapAddin $repo)) }
if (!$StageOnly) {
    $out = Assert-ExternalBuildPath $ReleaseRoot $repo
    if ($stage -eq $out -or $stage.StartsWith($out + '\', 'OrdinalIgnoreCase') -or $out.StartsWith($stage + '\', 'OrdinalIgnoreCase')) {
        throw 'Installer staging and release output must be separate'
    }
    $compiler = Assert-ExternalBuildPath $CompilerPath $repo
    $lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installer.lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ((Get-FileHash -LiteralPath $compiler).Hash.ToLowerInvariant() -ne $lock.compiler_sha256) {
        throw 'Inno Setup compiler does not match installer.lock.json'
    }
}
$raw = & $BuildPython -B (Join-Path $PSScriptRoot 'stage_installer.py') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Could not assemble the installer payload' }
$summary = $raw | ConvertFrom-Json
if ($StageOnly) {
    $raw
    exit 0
}
[IO.Directory]::CreateDirectory($out) | Out-Null
$exe = Join-Path $out "GuizhiSetup-$($summary.build).exe"
if (Test-Path -LiteralPath $exe) { throw 'Installer already exists' }
$speech = [int]($summary.components -contains 'speech')
$arcmap = [int]($summary.components -contains 'arcmap')
& $compiler /Qp "/DStage=$stage" "/DOutputDir=$out" "/DVersion=$($summary.version)" "/DBuild=$($summary.build)" "/DHasSpeech=$speech" "/DHasArcmap=$arcmap" (Join-Path $PSScriptRoot 'GuizhiSetup.iss')
if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Installer compilation failed' }
$summary | Add-Member -NotePropertyName installer -NotePropertyValue $exe
$summary | Add-Member -NotePropertyName installer_sha256 -NotePropertyValue ((Get-FileHash -LiteralPath $exe).Hash.ToLowerInvariant())
$summary | ConvertTo-Json -Depth 8
