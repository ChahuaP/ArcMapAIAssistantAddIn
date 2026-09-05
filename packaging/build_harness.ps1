# Build the ArcMap Harness deployment into build/harness-staging.
# Run from the repo root: pwsh -NoProfile -File packaging\build_harness.ps1
# Output layout (mirrors the runtime import layout):
#   harness/server, harness/gateway_py3, harness/shared_runtime,
#   harness/operation_catalog, harness/VERSION, harness/dsh/*,
#   OpenAssistantWeb.cmd, ArcMapAIAssistantAddIn.esriaddin
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent  # repo root
$stage = Join-Path $repo 'build\harness-staging'
$harness = Join-Path $stage 'harness'

if (Test-Path -LiteralPath $stage) { Remove-Item -Recurse -Force -LiteralPath $stage }
New-Item -ItemType Directory -Force -Path $harness | Out-Null

# The boundary server is self-contained: server/ + shared_runtime + catalog.
robocopy (Join-Path $repo 'server') (Join-Path $harness 'server') /E /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy server failed: $LASTEXITCODE" }
robocopy (Join-Path $repo 'shared_runtime') (Join-Path $harness 'shared_runtime') /E /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy shared_runtime failed: $LASTEXITCODE" }
robocopy (Join-Path $repo 'operation_catalog') (Join-Path $harness 'operation_catalog') /E /NFL /NDL /NJH /NJS | Out-Null
robocopy (Join-Path $repo 'arcmap_runtime_py2') (Join-Path $harness 'arcmap_runtime_py2') /E /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy arcmap_runtime_py2 failed: $LASTEXITCODE" }
if ($LASTEXITCODE -ge 8) { throw "robocopy operation_catalog failed: $LASTEXITCODE" }
Copy-Item (Join-Path $repo 'VERSION') (Join-Path $harness 'VERSION')

# dsh composition sources (profile + brand plugin) ship with the deployment.
robocopy (Join-Path $repo 'dsh') (Join-Path $harness 'dsh') /E /NFL /NDL /NJH /NJS /XD node_modules | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy dsh failed: $LASTEXITCODE" }

# Launcher that the ArcMap Add-in invokes.
Copy-Item (Join-Path $repo 'packaging\harness\OpenAssistantWeb.cmd') $stage
Copy-Item (Join-Path $repo 'packaging\harness\launch_harness.ps1') $harness

# Rebuild the Add-in package (button caption now "ArcMap Harness").
Push-Location (Join-Path $repo 'ArcMapAIAssistantAddIn')
try {
    python makeaddin.py
    if ($LASTEXITCODE -ne 0) { throw 'makeaddin.py failed' }
} finally { Pop-Location }
Copy-Item (Join-Path $repo 'ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin') $stage

# Syntax gate: a staged file that cannot compile must never reach the machine.
$pycache = Join-Path $env:TEMP ('harness-pycache-' + [guid]::NewGuid().ToString('N'))
$env:PYTHONPYCACHEPREFIX = $pycache
$staged = Get-ChildItem -Recurse -Filter *.py $harness | Select-Object -ExpandProperty FullName
python -m py_compile @staged
if ($LASTEXITCODE -ne 0) { throw 'py_compile failed on staged harness sources' }
Remove-Item -Recurse -Force $pycache -ErrorAction SilentlyContinue

# Deployment identity: version + build time + content digest.
$version = (Get-Content (Join-Path $repo 'VERSION') -Raw).Trim()
$files = Get-ChildItem -Recurse -File $stage | Sort-Object FullName
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
