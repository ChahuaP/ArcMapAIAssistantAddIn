# Remove every previously installed GeoPilot / ArcMapAIAssistant / ArcMap
# Harness version before the new ArcMap Harness deployment lands. Handles:
#   * 1.x portable installs under %LOCALAPPDATA%\ArcMapAIAssistant\app
#   * 2.0 Inno Setup installs under Program Files\GeoPilot (and its registry
#     Add/Remove entry, so no orphaned "GeoPilot" row is left behind)
#   * the ArcMap Add-in packages (single stable GUID) in every Desktop10.x
#   * stale Start Menu folders, running gateway/bridge/harness processes
# User data (API keys, model config, custom tools, workflows, logs) is kept
# unless -RemoveUserConfig is given.
param(
    [switch]$RemoveUserConfig,
    [switch]$Quiet
)
$ErrorActionPreference = 'SilentlyContinue'

function Say([string]$Text) { if (-not $Quiet) { Write-Output $Text } }

$AddinId = '{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}'
$AppDataName = 'ArcMapAIAssistant'

# -- stop old processes -------------------------------------------------------
Say 'stop old processes'
foreach ($name in 'ArcMapBridge', 'ArcMapAIAssistantGateway') {
    Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -in @('node.exe', 'python.exe', 'pythonw.exe') -and (
        $_.CommandLine -like '*--profile arcmap-harness*' -or
        $_.CommandLine -like '*\harness\server\main.py*' -or
        $_.CommandLine -like '*GeoPilot\harness*')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($port in 8765, 8766) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Milliseconds 600

# -- registry: drop old Add/Remove entries ------------------------------------
Say 'remove old uninstall registry entries'
$uninstallRoots = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
)
$namePattern = '^(GeoPilot|ArcMapAIAssistant|ArcMap AI Assistant|ArcMap Harness)\b'
foreach ($root in $uninstallRoots) {
    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
        $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
        if ($props.DisplayName -match $namePattern) {
            Say ("  key {0} ({1})" -f $_.PSChildName, $props.DisplayName)
            Remove-Item $_.PSPath -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# -- install directories ------------------------------------------------------
function Remove-InstallDir([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Container)) { return }
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd('\') -eq $root.TrimEnd('\')) { return }
    # only remove directories that really look like one of our installs
    $markers = @(
        (Join-Path $full 'arcmap_runtime_py2\runtime.py'),
        (Join-Path $full 'harness\server\main.py'),
        (Join-Path $full 'gateway\ArcMapAIAssistantGateway.exe'),
        (Join-Path $full 'bridge\ArcMapBridge.exe')
    )
    $looksOurs = $false
    foreach ($marker in $markers) { if (Test-Path -LiteralPath $marker) { $looksOurs = $true; break } }
    if (-not $looksOurs) {
        Say "  skip (not an ArcMap Harness install): $full"
        return
    }
    Say "  remove $full"
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
}

Say 'remove old install directories'
# 1.x portable program tree
Remove-InstallDir (Join-Path $env:LOCALAPPDATA "$AppDataName\app")
# old install.json recorded install_dir (covers custom install paths)
$oldConfig = Join-Path $env:APPDATA "$AppDataName\install.json"
if (Test-Path -LiteralPath $oldConfig) {
    try {
        $payload = Get-Content -LiteralPath $oldConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($payload.install_dir) { Remove-InstallDir ([string]$payload.install_dir) }
    } catch { }
}
foreach ($candidate in @(
    'C:\Program Files\GeoPilot',
    'C:\Program Files\ArcMapAIAssistant',
    'C:\Program Files (x86)\GeoPilot',
    'C:\Program Files (x86)\ArcMapAIAssistant',
    'D:\GeoPilot',
    'D:\ArcMapAIAssistant',
    (Join-Path $env:LOCALAPPDATA 'Programs\GeoPilot'),
    (Join-Path $env:LOCALAPPDATA 'Programs\ArcMapAIAssistant')
)) { Remove-InstallDir $candidate }

# -- Start Menu folders -------------------------------------------------------
Say 'remove old Start Menu folders'
foreach ($menuRoot in @(
    (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
)) {
    foreach ($folder in 'GeoPilot', 'ArcMapAIAssistant', 'ArcMap AI Assistant', 'ArcMap Harness') {
        $path = Join-Path $menuRoot $folder
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

# -- ArcMap Add-in packages ---------------------------------------------------
Say 'remove old ArcMap Add-in packages'
$addinRoot = Join-Path $env:USERPROFILE 'Documents\ArcGIS\AddIns'
if (Test-Path -LiteralPath $addinRoot) {
    Get-ChildItem -LiteralPath $addinRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^Desktop10\.\d+$' } | ForEach-Object {
            $target = Join-Path $_.FullName $AddinId
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue }
        }
}

# -- stale runtime state / old config ----------------------------------------
Say 'remove stale runtime state'
$localData = Join-Path $env:LOCALAPPDATA $AppDataName
foreach ($stale in 'bridge.ready', 'bridge_command.json', 'harness_url.txt', 'launch_harness.ps1') {
    $path = Join-Path $localData $stale
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
}
if (Test-Path -LiteralPath $oldConfig) { Remove-Item -LiteralPath $oldConfig -Force -ErrorAction SilentlyContinue }

if ($RemoveUserConfig) {
    Say 'remove user data'
    Remove-Item -LiteralPath (Join-Path $env:APPDATA $AppDataName) -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $localData -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Say 'kept user data (API key, model config, custom tools, workflows, logs)'
}

Say 'legacy cleanup done'
exit 0
