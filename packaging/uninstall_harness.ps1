# Uninstall ArcMap Harness. Invoked by the Inno Setup uninstaller, or by hand:
#   pwsh -NoProfile -File packaging\uninstall_harness.ps1 -InstallDir "C:\Program Files\ArcMap Harness"
# Removes the program tree, the ArcMap Add-in package, the dsh profile and the
# install.json pointer. User data (API key, model config, custom tools,
# workflows, logs) is kept unless -RemoveUserConfig is given.
param(
    [string]$InstallDir = 'C:\Program Files\ArcMap Harness',
    [switch]$RemoveUserConfig,
    [switch]$Quiet
)
$ErrorActionPreference = 'SilentlyContinue'

function Say([string]$Text) { if (-not $Quiet) { Write-Output $Text } }

$AddinId = '{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}'
$AppDataName = 'ArcMapAIAssistant'

Say 'stop processes'
Get-Process -Name ArcMapBridge, ArcMapAIAssistantGateway -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -in @('node.exe', 'python.exe', 'pythonw.exe') -and (
        $_.CommandLine -like '*--profile arcmap-harness*' -or
        $_.CommandLine -like '*\harness\server\main.py*')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($port in 8765, 8766) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Milliseconds 600

Say 'remove ArcMap Add-in packages'
$addinRoot = Join-Path $env:USERPROFILE 'Documents\ArcGIS\AddIns'
if (Test-Path -LiteralPath $addinRoot) {
    Get-ChildItem -LiteralPath $addinRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^Desktop10\.\d+$' } | ForEach-Object {
            $target = Join-Path $_.FullName $AddinId
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue }
        }
}

Say 'remove program tree'
if ($InstallDir -and (Test-Path -LiteralPath $InstallDir -PathType Container)) {
    $full = [System.IO.Path]::GetFullPath($InstallDir)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd('\') -ne $root.TrimEnd('\')) {
        Remove-Item -LiteralPath (Join-Path $full 'harness') -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $full 'harness_identity.json') -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $full 'packaging') -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Say 'remove dsh profile'
$localData = Join-Path $env:LOCALAPPDATA $AppDataName
$DshHome = Join-Path $localData 'dsh-home'
foreach ($relative in 'profiles\arcmap-harness', 'plugins\arcmap-brand', 'plugins\arcmap-status') {
    $path = Join-Path $DshHome $relative
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue }
}
foreach ($stale in 'install.json', 'bridge.ready', 'bridge_command.json', 'harness_url.txt', 'launch_harness.ps1') {
    $path = Join-Path $env:APPDATA "$AppDataName\$stale"
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    $path = Join-Path $localData $stale
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
}

if ($RemoveUserConfig) {
    Say 'remove user data'
    Remove-Item -LiteralPath (Join-Path $env:APPDATA $AppDataName) -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $localData -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Say 'kept user data (API key, model config, custom tools, workflows, logs)'
}

Say 'uninstall done'
exit 0
