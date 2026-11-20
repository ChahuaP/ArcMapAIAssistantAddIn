
param(
    [Parameter(Mandatory)][string]$StageRoot,
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$DesktopRuntime,
    [Parameter(Mandatory)][string]$DshRuntime,
    [Parameter(Mandatory)][string]$SourceTree,
    [string]$SourceCommit,
    [string]$SpeechRuntime,
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
$setup = Join-Path $PSScriptRoot 'GuizhiSetup.iss'
$product = [regex]::Match((Get-Content -LiteralPath $setup -Raw -Encoding UTF8), '(?m)^#define ProductName "([^"]+)"\r?$')
if (!$product.Success) { throw 'Installer product name is missing' }
$exe = Join-Path $out "$($product.Groups[1].Value)-$($summary.version).exe"
if (Test-Path -LiteralPath $exe) { throw 'Installer already exists' }
& $compiler /Qp "/DStage=$stage" "/DOutputDir=$out" "/DVersion=$($summary.version)" "/DBuild=$($summary.build)" $setup
if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Installer compilation failed' }
$summary | Add-Member -NotePropertyName installer -NotePropertyValue $exe
$summary | Add-Member -NotePropertyName installer_sha256 -NotePropertyValue ((Get-FileHash -LiteralPath $exe).Hash.ToLowerInvariant())
foreach ($property in $summary.components.PSObject.Properties) {
    $component = $property.Value
    Copy-Item -LiteralPath (Join-Path $stage "components/$($component.file)") -Destination $out
}
$summary | ConvertTo-Json -Depth 8
