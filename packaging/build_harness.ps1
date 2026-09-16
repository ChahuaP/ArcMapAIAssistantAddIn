# Build the ArcMap Harness deployment into build/harness-staging.
# Run from the repo root: pwsh -NoProfile -File packaging\build_harness.ps1
# Output layout (self-contained under harness/, mirrors the runtime import
# layout so a single install root works for both the Py3 boundary server and
# the ArcMap Py2 runtime):
#   harness/server, harness/shared_runtime, harness/operation_catalog,
#   harness/arcmap_runtime_py2, harness/bridge, harness/dsh, harness/VERSION,
#   harness/OpenAssistantWeb.cmd, harness/launch_harness.ps1
#   ArcMapAIAssistantAddIn.esriaddin, harness_identity.json
param(
    [switch]$SkipBridge
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent  # repo root
$stage = Join-Path $repo 'build\harness-staging'
$harness = Join-Path $stage 'harness'

if (Test-Path -LiteralPath $stage) { Remove-Item -Recurse -Force -LiteralPath $stage }
New-Item -ItemType Directory -Force -Path $harness | Out-Null

# The boundary server is self-contained: server/ + shared_runtime + catalog.
robocopy (Join-Path $repo 'server') (Join-Path $harness 'server') /E /R:2 /W:1 /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy server failed: $LASTEXITCODE" }
robocopy (Join-Path $repo 'shared_runtime') (Join-Path $harness 'shared_runtime') /E /R:2 /W:1 /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy shared_runtime failed: $LASTEXITCODE" }
robocopy (Join-Path $repo 'operation_catalog') (Join-Path $harness 'operation_catalog') /E /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy operation_catalog failed: $LASTEXITCODE" }
robocopy (Join-Path $repo 'arcmap_runtime_py2') (Join-Path $harness 'arcmap_runtime_py2') /E /R:2 /W:1 /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy arcmap_runtime_py2 failed: $LASTEXITCODE" }
Copy-Item (Join-Path $repo 'VERSION') (Join-Path $harness 'VERSION')

# dsh composition sources (profile + brand plugin) ship with the deployment.
robocopy (Join-Path $repo 'dsh') (Join-Path $harness 'dsh') /E /R:2 /W:1 /NFL /NDL /NJH /NJS /XD node_modules | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy dsh failed: $LASTEXITCODE" }

# C# Bridge: build and stage next to the Py2 runtime so runtime.py resolves
# <install>\bridge\ArcMapBridge.exe. Only shipped dependencies are copied (the
# ESRI interop assemblies come from the local ArcGIS install); XML doc files
# are dropped.
if (-not $SkipBridge) {
    & (Join-Path $repo 'ArcMapBridgeExternal\build.ps1') | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'ArcMapBridgeExternal\build.ps1 failed' }
}
$bridgeOut = Join-Path $repo 'ArcMapBridgeExternal\bin\Release'
if (-not (Test-Path (Join-Path $bridgeOut 'ArcMapBridge.exe'))) {
    throw "Bridge output missing: $bridgeOut\ArcMapBridge.exe"
}
robocopy $bridgeOut (Join-Path $harness 'bridge') /E /R:2 /W:1 /NFL /NDL /NJH /NJS /XF *.xml *.pdb | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy bridge failed: $LASTEXITCODE" }
# Bridge and Py2 runtime must share the same immutable deployment identity.
Copy-Item (Join-Path $repo 'arcmap_runtime_py2\deployment_identity.json') (Join-Path $harness 'bridge\deployment_identity.json') -Force

# Launcher that the ArcMap Add-in invokes (co-located with runtime.py).
Copy-Item (Join-Path $repo 'packaging\harness\OpenAssistantWeb.cmd') $harness
Copy-Item (Join-Path $repo 'packaging\harness\launch_harness.ps1') $harness
Copy-Item (Join-Path $repo 'packaging\harness\configure_ollama.ps1') $harness

# Fully self-contained runtime (portable Node + standalone dsh + embeddable
# Python + pre-built dsh profile) so the installer needs no user environment.
& (Join-Path $PSScriptRoot 'build_runtime.ps1') -Stage $stage

# Rebuild the Add-in package (button caption "ArcMap Harness").
Push-Location (Join-Path $repo 'ArcMapAIAssistantAddIn')
try {
    python makeaddin.py
    if ($LASTEXITCODE -ne 0) { throw 'makeaddin.py failed' }
} finally { Pop-Location }
Copy-Item (Join-Path $repo 'ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin') $stage

# Syntax gate: a staged file that cannot compile must never reach the machine.
$pycache = Join-Path $env:TEMP ('harness-pycache-' + [guid]::NewGuid().ToString('N'))
$env:PYTHONPYCACHEPREFIX = $pycache
$gateDirs = @('server', 'shared_runtime', 'arcmap_runtime_py2') | ForEach-Object { Join-Path $harness $_ }
$staged = Get-ChildItem -Recurse -Filter *.py $gateDirs | Select-Object -ExpandProperty FullName
python -m py_compile @staged
if ($LASTEXITCODE -ne 0) { throw 'py_compile failed on staged harness sources' }
Remove-Item -Recurse -Force $pycache -ErrorAction SilentlyContinue

# Deployment identity: version + build time + content digest.
$version = (Get-Content (Join-Path $repo 'VERSION') -Raw).Trim()
$files = Get-ChildItem -Recurse -File $stage |
    Where-Object { $_.FullName -notmatch '\\harness\\(runtime|dsh-profile)\\' } | Sort-Object FullName
$hashes = foreach ($file in $files) {
    (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$combined = ($hashes -join '')
$sha = [System.Security.Cryptography.SHA256]::Create()
$digest = [BitConverter]::ToString(
    $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($combined))).Replace('-', '').ToLowerInvariant()
$identity = [ordered]@{
    harness_version = $version
    built_at = (Get-Date).ToUniversalTime().ToString('o')
    deployment_hash = $digest
    file_count = $files.Count
}
$identity | ConvertTo-Json | Set-Content (Join-Path $stage 'harness_identity.json') -Encoding UTF8
Write-Output ("built: {0} v{1} ({2} files, hash {3})" -f $stage, $version, $files.Count, $digest.Substring(0, 12))
