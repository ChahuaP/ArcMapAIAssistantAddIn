# Install the built ArcMap Harness deployment to this machine, replacing any
# previous version. Normally invoked by the Inno Setup package
# (ArcMapHarnessSetup-<version>.exe); for a developer install run after
# packaging\build_harness.ps1:
#   pwsh -NoProfile -File packaging\install_harness.ps1
# Elevates itself when not admin (UAC prompt). Deploys:
#   C:\Program Files\ArcMap Harness\harness\   (self-contained: server, Py2
#                                               runtime, catalog, bridge, and a
#                                               bundled Node + dsh + Python)
#   Documents\ArcGIS\AddIns\Desktop10.x\{guid}\arcmapaiassistantaddin.esriaddin
#   %APPDATA%\ArcMapAIAssistant\install.json
#   %LOCALAPPDATA%\ArcMapAIAssistant\dsh-home  (dsh profile, per-user)
param(
    [string]$Stage = '',
    [string]$InstallDir = '',
    [switch]$NoElevate
)
$ErrorActionPreference = 'Stop'

# -- self-elevation -----------------------------------------------------------
if (-not $NoElevate) {
    $identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $repo = Split-Path $PSScriptRoot -Parent
        if (-not $Stage) { $Stage = Join-Path $repo 'build\harness-staging' }
        $logDir = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant\logs'
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $log = Join-Path $logDir 'install_harness.log'
        $elevArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
            "& { & '$(Join-Path $PSScriptRoot 'install_harness.ps1')' -Stage '$Stage' -InstallDir '$InstallDir' *>&1 | Tee-Object -FilePath '$log' }")
        $proc = Start-Process pwsh -Verb RunAs -PassThru -Wait -ArgumentList $elevArgs
        exit $proc.ExitCode
    }
}

# -- transcript (diagnosable one-click installs) ------------------------------
$logDir = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
try { Start-Transcript -Path (Join-Path $logDir 'install_harness.log') -Append -Force | Out-Null } catch { }

$repo = Split-Path $PSScriptRoot -Parent
if (-not $Stage) { $Stage = Join-Path $repo 'build\harness-staging' }
if (-not (Test-Path (Join-Path $Stage 'harness_identity.json'))) {
    throw "staging 缺少 harness_identity.json：$Stage。先运行 packaging\build_harness.ps1。"
}
if (-not $InstallDir) { $InstallDir = 'C:\Program Files\ArcMap Harness' }
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
$HarnessDir = Join-Path $InstallDir 'harness'
$AddinId = '{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}'

Write-Output "install root: $InstallDir"

# -- stop ArcMap (releases the Add-in), the console, boundary and bridge -------
Get-Process -Name ArcMap -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Output ("stop ArcMap {0}" -f $_.Id)
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -in @('node.exe', 'python.exe', 'ArcMapBridge.exe', 'ArcMapAIAssistantGateway.exe')) -and (
        $_.CommandLine -like '*--profile arcmap-harness*' -or
        $_.CommandLine -like '*ArcMap Harness\harness\server\main.py*' -or
        $_.Name -in @('ArcMapBridge.exe', 'ArcMapAIAssistantGateway.exe'))
} | ForEach-Object {
    Write-Output ("stop {0} {1}" -f $_.ProcessId, $_.Name)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 800

# -- deploy the self-contained harness tree -----------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
# dsh-profile is staged into Program Files only to reach the temp package; it is
# deployed to the per-user dsh-home below, never left in the program tree.
robocopy (Join-Path $Stage 'harness') $HarnessDir /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /XD __pycache__ dsh-profile | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy harness failed: $LASTEXITCODE" }
Copy-Item (Join-Path $Stage 'harness_identity.json') (Join-Path $InstallDir 'harness_identity.json') -Force

# drop stale top-level trees left by older layouts of this install root
foreach ($legacy in 'arcmap_runtime_py2', 'shared_runtime', 'operation_catalog', 'server', 'bridge', 'gateway', 'dsh') {
    $path = Join-Path $InstallDir $legacy
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue }
}

# -- the bundled interpreter dsh spawns for the MCP server --------------------
$pythonExe = Join-Path $HarnessDir 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw "缺少内置 Python 运行时：$pythonExe" }
$pythonExe = $pythonExe -replace '\\', '/'

# -- write install.json so the Add-in can locate the runtime ------------------
$identityDoc = Get-Content (Join-Path $Stage 'harness_identity.json') -Raw | ConvertFrom-Json
$appVersion = [string]$identityDoc.harness_version
$addinRoot = Join-Path $env:USERPROFILE 'Documents\ArcGIS\AddIns'
$desktopVersions = @()
if (Test-Path -LiteralPath $addinRoot) {
    $desktopVersions = @(Get-ChildItem -LiteralPath $addinRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^Desktop10\.\d+$' } | Sort-Object Name | Select-Object -ExpandProperty Name)
}
$addinTargetDirs = @()
foreach ($version in $desktopVersions) { $addinTargetDirs += (Join-Path $addinRoot "$version\$AddinId") }

$configDir = Join-Path $env:APPDATA 'ArcMapAIAssistant'
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$installConfig = [ordered]@{
    install_dir = $HarnessDir
    app_version = $appVersion
    addin_dirs = $addinTargetDirs
    addin_dir = if ($addinTargetDirs.Count) { $addinTargetDirs[0] } else { '' }
    bridge_exe = (Join-Path $HarnessDir 'bridge\ArcMapBridge.exe')
    desktop_versions = $desktopVersions
    desktop_version = if ($desktopVersions.Count) { $desktopVersions[0] } else { '' }
    installed_at = (Get-Date).ToString('s')
    deployment_hash = [string]$identityDoc.deployment_hash
}
$installConfig | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $configDir 'install.json') -Encoding UTF8

# -- replace the ArcMap Add-in packages ---------------------------------------
$addinPackage = Join-Path $Stage 'ArcMapAIAssistantAddIn.esriaddin'
$placed = 0
if ($addinTargetDirs.Count -eq 0) {
    Write-Output "warn: 未找到 Documents\ArcGIS\AddIns\Desktop10.x，跳过 Add-in 部署"
} else {
    foreach ($addinTargetDir in $addinTargetDirs) {
        New-Item -ItemType Directory -Force -Path $addinTargetDir | Out-Null
        Copy-Item $addinPackage (Join-Path $addinTargetDir 'arcmapaiassistantaddin.esriaddin') -Force
        $placed++
    }
}
Write-Output "addin packages replaced: $placed"

# One profile source for full installs and console-only updates.
& (Join-Path $PSScriptRoot 'install_profile.ps1') -Stage $Stage -HarnessDir $HarnessDir -CopyDependencies
$DshHome = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant\dsh-home'
$ProfileDir = Join-Path $DshHome 'profiles\arcmap-harness'

# Seed the provider key from the current environment once, if set.
$envFile = Join-Path $DshHome '.env'
if (-not (Test-Path $envFile) -and $env:MINIMAX_API_KEY) {
    Set-Content -Path $envFile -Value ("MINIMAX_API_KEY=" + $env:MINIMAX_API_KEY) -Encoding ASCII
}

# -- report -------------------------------------------------------------------
Write-Output "INSTALLED: $($identityDoc | ConvertTo-Json -Compress)"
Write-Output ("profile: {0}" -f $ProfileDir)
Write-Output ("harness: {0}" -f $HarnessDir)
Write-Output ("addin:   {0}" -f ($addinTargetDirs -join ', '))
try { Stop-Transcript | Out-Null } catch { }
