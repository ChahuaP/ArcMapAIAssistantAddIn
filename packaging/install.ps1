param(
    [string]$InstallDir = "",
    [string]$DesktopVersion = "Desktop10.1",
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

function Get-PackageRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    if (Test-Path -LiteralPath (Join-Path $scriptDir "app")) {
        return (Resolve-Path $scriptDir).Path
    }
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Select-InstallDir {
    param([string]$Requested)
    if ($Requested) {
        return $Requested
    }
    $defaultDir = Join-Path $env:ProgramFiles "GeoPilot"
    if ($Quiet) {
        return $defaultDir
    }

    Write-Host ""
    Write-Host "请选择安装位置："
    Write-Host "1. $defaultDir"
    if (Test-Path -LiteralPath "D:\") {
        Write-Host "2. D:\ArcMapAIAssistant"
        Write-Host "3. 自定义路径"
    } else {
        Write-Host "2. 自定义路径"
    }
    $choice = Read-Host "请输入序号，直接回车默认安装到 $defaultDir"
    if (-not $choice) {
        return $defaultDir
    }
    if ($choice -eq "1") {
        return $defaultDir
    }
    if ((Test-Path -LiteralPath "D:\") -and $choice -eq "2") {
        return "D:\ArcMapAIAssistant"
    }
    $custom = Read-Host "请输入完整安装路径"
    if (-not $custom) {
        throw "没有输入安装路径。"
    }
    return $custom
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

function Test-InstallHealth {
    param([string]$TargetRoot, [string]$AddinTargetDir)
    $required = @(
        (Join-Path $TargetRoot "arcmap_runtime_py2\runtime.py"),
        (Join-Path $TargetRoot "operation_catalog\catalog.json"),
        (Join-Path $TargetRoot "gateway\ArcMapAIAssistantGateway.exe"),
        (Join-Path $TargetRoot "OpenAssistantWeb.cmd"),
        (Join-Path $TargetRoot "StartGateway.cmd"),
        (Join-Path $TargetRoot "help.html"),
        (Join-Path $TargetRoot "uninstall.ico"),
        (Join-Path $TargetRoot "VERSION"),
        (Join-Path $AddinTargetDir "arcmapaiassistantaddin.esriaddin")
    )
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
$runtimeSource = Join-Path $appSource "arcmap_runtime_py2"
$catalogSource = Join-Path $appSource "operation_catalog"
$openCmd = Join-Path $appSource "OpenAssistantWeb.cmd"
$startCmd = Join-Path $appSource "StartGateway.cmd"
$helpHtml = Join-Path $appSource "help.html"
$uninstallIcon = Join-Path $appSource "uninstall.ico"
$versionFile = Join-Path $appSource "VERSION"

Require-File $addin "缺少 ArcMap 插件包：$addin"
Require-File $gatewayExe "缺少 Python3 网关 EXE：$gatewayExe。请先用 packaging\build_release.ps1 生成发布包。"
Require-File (Join-Path $runtimeSource "runtime.py") "缺少 ArcMap runtime：$runtimeSource"
Require-File (Join-Path $catalogSource "catalog.json") "缺少操作目录：$catalogSource"
Require-File $openCmd "缺少打开控制台脚本：$openCmd"
Require-File $startCmd "缺少启动后台脚本：$startCmd"
Require-File $helpHtml "缺少帮助文件：$helpHtml"
Require-File $uninstallIcon "缺少卸载图标：$uninstallIcon"
Require-File $versionFile "缺少版本文件：$versionFile"
$appVersion = (Get-Content -Encoding UTF8 -LiteralPath $versionFile -Raw).Trim()

$targetRoot = Select-InstallDir $InstallDir
$targetRoot = [System.IO.Path]::GetFullPath($targetRoot)
if ($targetRoot.StartsWith($env:ProgramFiles, [System.StringComparison]::OrdinalIgnoreCase) -and -not (Test-IsAdministrator)) {
    throw "安装到 $env:ProgramFiles 需要管理员权限。请使用 InstallArcMapAIAssistant.cmd 或安装器启动，它会自动请求管理员权限。"
}

Write-Host ""
Write-Host "正在安装到：$targetRoot"
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null

Copy-CleanDirectory $runtimeSource (Join-Path $targetRoot "arcmap_runtime_py2")
Copy-CleanDirectory $catalogSource (Join-Path $targetRoot "operation_catalog")
Copy-CleanDirectory (Join-Path $appSource "gateway") (Join-Path $targetRoot "gateway")
Copy-Item -LiteralPath $openCmd -Destination (Join-Path $targetRoot "OpenAssistantWeb.cmd") -Force
Copy-Item -LiteralPath $startCmd -Destination (Join-Path $targetRoot "StartGateway.cmd") -Force
Copy-Item -LiteralPath $helpHtml -Destination (Join-Path $targetRoot "help.html") -Force
Copy-Item -LiteralPath $uninstallIcon -Destination (Join-Path $targetRoot "uninstall.ico") -Force
Copy-Item -LiteralPath $versionFile -Destination (Join-Path $targetRoot "VERSION") -Force

$configDir = Join-Path $env:APPDATA "ArcMapAIAssistant"
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
$addinId = "{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}"
$addinTargetDir = Join-Path $HOME "Documents\ArcGIS\AddIns\$DesktopVersion\$addinId"
$installConfig = @{
    install_dir = $targetRoot
    app_version = $appVersion
    addin_dir = $addinTargetDir
    desktop_version = $DesktopVersion
    installed_at = (Get-Date).ToString("s")
}
$installConfig | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $configDir "install.json") -Encoding UTF8

New-Item -ItemType Directory -Path $addinTargetDir -Force | Out-Null
Copy-Item -LiteralPath $addin -Destination (Join-Path $addinTargetDir "arcmapaiassistantaddin.esriaddin") -Force
Test-InstallHealth $targetRoot $addinTargetDir

Write-Host ""
Write-Host "安装完成。"
Write-Host "安装自检：通过。"
Write-Host "ArcMap 插件目录：$addinTargetDir"
Write-Host "程序目录：$targetRoot"
Write-Host "配置文件：$(Join-Path $configDir "install.json")"
