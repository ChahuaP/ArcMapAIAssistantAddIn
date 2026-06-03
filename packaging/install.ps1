param(
    [string]$InstallDir = "",
    [string]$DesktopVersion = "",
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

function Get-PackageRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Require-File {
    param([string]$Path, [string]$Message)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw $Message
    }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Copy-CleanDirectory {
    param([string]$Source, [string]$Destination)
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Get-ArcMapDesktopVersions {
    if ($DesktopVersion) {
        return @($DesktopVersion)
    }
    $addinRoot = Join-Path $HOME "Documents\ArcGIS\AddIns"
    if (-not (Test-Path -LiteralPath $addinRoot)) {
        return @("Desktop10.1")
    }
    $versions = Get-ChildItem -LiteralPath $addinRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "^Desktop10\.\d+$" } |
        Sort-Object Name |
        Select-Object -ExpandProperty Name
    if (-not $versions) {
        return @("Desktop10.1")
    }
    return @($versions)
}

function Test-InstallHealth {
    param([string]$TargetRoot, [string[]]$AddinTargetDirs)
    $required = @(
        (Join-Path $TargetRoot "arcmap_runtime_py2\runtime.py"),
        (Join-Path $TargetRoot "operation_catalog\catalog.json"),
        (Join-Path $TargetRoot "gateway\ArcMapAIAssistantGateway.exe"),
        (Join-Path $TargetRoot "bridge\ArcMapBridge.exe"),
        (Join-Path $TargetRoot "OpenAssistantWeb.cmd"),
        (Join-Path $TargetRoot "StartGateway.cmd"),
        (Join-Path $TargetRoot "uninstall.ico"),
        (Join-Path $TargetRoot "VERSION")
    )
    foreach ($addinTargetDir in $AddinTargetDirs) {
        $required += (Join-Path $addinTargetDir "arcmapaiassistantaddin.esriaddin")
    }
    $missing = @()
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path)) {
            $missing += $path
        }
    }
    if ($missing.Count -gt 0) {
        throw ("安装自检失败，缺少：" + [Environment]::NewLine + ($missing -join [Environment]::NewLine))
    }
}

$packageRoot = Get-PackageRoot
$appSource = Join-Path $packageRoot "app"
$addin = Join-Path $packageRoot "ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin"
$gatewayExe = Join-Path $appSource "gateway\ArcMapAIAssistantGateway.exe"
$bridgeExe = Join-Path $appSource "bridge\ArcMapBridge.exe"
$runtimeSource = Join-Path $appSource "arcmap_runtime_py2"
$catalogSource = Join-Path $appSource "operation_catalog"
$openCmd = Join-Path $appSource "OpenAssistantWeb.cmd"
$startCmd = Join-Path $appSource "StartGateway.cmd"
$uninstallIcon = Join-Path $appSource "uninstall.ico"
$versionFile = Join-Path $appSource "VERSION"

Require-File $addin "缺少 ArcMap 插件包：$addin"
Require-File $gatewayExe "缺少 Python3 网关 EXE：$gatewayExe。请先用 packaging\build_release.ps1 生成发布包。"
Require-File $bridgeExe "缺少 ArcMapBridge.exe：$bridgeExe"
Require-File (Join-Path $runtimeSource "runtime.py") "缺少 ArcMap runtime：$runtimeSource"
Require-File (Join-Path $catalogSource "catalog.json") "缺少操作目录：$catalogSource"
Require-File $openCmd "缺少打开控制台脚本：$openCmd"
Require-File $startCmd "缺少启动后台脚本：$startCmd"
Require-File $uninstallIcon "缺少卸载图标：$uninstallIcon"
Require-File $versionFile "缺少版本文件：$versionFile"
$appVersion = (Get-Content -Encoding UTF8 -LiteralPath $versionFile -Raw).Trim()

if (-not $InstallDir) {
    throw "InstallDir is required. Please run GeoPilotSetup-$appVersion.exe."
}
$targetRoot = $InstallDir
$targetRoot = [System.IO.Path]::GetFullPath($targetRoot)
if ($targetRoot.StartsWith($env:ProgramFiles, [System.StringComparison]::OrdinalIgnoreCase) -and -not (Test-IsAdministrator)) {
    throw "安装到 $env:ProgramFiles 需要管理员权限。请使用 GeoPilotSetup-$appVersion.exe。"
}

Write-Host ""
Write-Host "正在安装到：$targetRoot"
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null

Copy-CleanDirectory $runtimeSource (Join-Path $targetRoot "arcmap_runtime_py2")
Copy-CleanDirectory $catalogSource (Join-Path $targetRoot "operation_catalog")
Copy-CleanDirectory (Join-Path $appSource "gateway") (Join-Path $targetRoot "gateway")
Copy-CleanDirectory (Join-Path $appSource "bridge") (Join-Path $targetRoot "bridge")
Copy-Item -LiteralPath $openCmd -Destination (Join-Path $targetRoot "OpenAssistantWeb.cmd") -Force
Copy-Item -LiteralPath $startCmd -Destination (Join-Path $targetRoot "StartGateway.cmd") -Force
Copy-Item -LiteralPath $uninstallIcon -Destination (Join-Path $targetRoot "uninstall.ico") -Force
Copy-Item -LiteralPath $versionFile -Destination (Join-Path $targetRoot "VERSION") -Force

$configDir = Join-Path $env:APPDATA "ArcMapAIAssistant"
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
$addinId = "{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}"
$desktopVersions = @(Get-ArcMapDesktopVersions)
$addinTargetDirs = @()
foreach ($version in $desktopVersions) {
    $addinTargetDirs += (Join-Path $HOME "Documents\ArcGIS\AddIns\$version\$addinId")
}
$installConfig = @{
    install_dir = $targetRoot
    app_version = $appVersion
    addin_dirs = $addinTargetDirs
    addin_dir = $addinTargetDirs[0]
    bridge_exe = (Join-Path $targetRoot "bridge\ArcMapBridge.exe")
    desktop_versions = $desktopVersions
    desktop_version = $desktopVersions[0]
    installed_at = (Get-Date).ToString("s")
}
$installConfig | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $configDir "install.json") -Encoding UTF8

foreach ($addinTargetDir in $addinTargetDirs) {
    New-Item -ItemType Directory -Path $addinTargetDir -Force | Out-Null
    Copy-Item -LiteralPath $addin -Destination (Join-Path $addinTargetDir "arcmapaiassistantaddin.esriaddin") -Force
}
Test-InstallHealth $targetRoot $addinTargetDirs

Write-Host ""
Write-Host "安装完成。"
Write-Host "安装自检：通过。"
Write-Host "ArcMap 插件目录：$($addinTargetDirs -join ', ')"
Write-Host "ArcMapBridge：$(Join-Path $targetRoot "bridge\ArcMapBridge.exe")"
Write-Host "程序目录：$targetRoot"
Write-Host "配置文件：$(Join-Path $configDir "install.json")"
