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
    $defaultDir = if (Test-Path -LiteralPath "D:\") { "D:\ArcMapAIAssistant" } else { "C:\Program Files\ArcMapAIAssistant" }
    if ($Quiet) {
        return $defaultDir
    }

    Write-Host ""
    Write-Host "请选择安装位置："
    Write-Host "1. C:\Program Files\ArcMapAIAssistant"
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
        return "C:\Program Files\ArcMapAIAssistant"
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

$packageRoot = Get-PackageRoot
$appSource = Join-Path $packageRoot "app"
$addin = Join-Path $packageRoot "ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin"
$gatewayExe = Join-Path $appSource "gateway\ArcMapAIAssistantGateway.exe"
$runtimeSource = Join-Path $appSource "arcmap_runtime_py2"
$openCmd = Join-Path $appSource "OpenAssistantWeb.cmd"
$startCmd = Join-Path $appSource "StartGateway.cmd"

Require-File $addin "缺少 ArcMap 插件包：$addin"
Require-File $gatewayExe "缺少 Python3 网关 EXE：$gatewayExe。请先用 packaging\build_release.ps1 生成发布包。"
Require-File (Join-Path $runtimeSource "runtime.py") "缺少 ArcMap runtime：$runtimeSource"
Require-File $openCmd "缺少打开控制台脚本：$openCmd"
Require-File $startCmd "缺少启动后台脚本：$startCmd"

$targetRoot = Select-InstallDir $InstallDir
$targetRoot = [System.IO.Path]::GetFullPath($targetRoot)
if ($targetRoot.StartsWith($env:ProgramFiles, [System.StringComparison]::OrdinalIgnoreCase) -and -not (Test-IsAdministrator)) {
    throw "安装到 $env:ProgramFiles 需要管理员权限。请右键用管理员身份运行安装程序，或选择 D:\ArcMapAIAssistant。"
}

Write-Host ""
Write-Host "正在安装到：$targetRoot"
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null

Copy-CleanDirectory $runtimeSource (Join-Path $targetRoot "arcmap_runtime_py2")
Copy-CleanDirectory (Join-Path $appSource "gateway") (Join-Path $targetRoot "gateway")
Copy-Item -LiteralPath $openCmd -Destination (Join-Path $targetRoot "OpenAssistantWeb.cmd") -Force
Copy-Item -LiteralPath $startCmd -Destination (Join-Path $targetRoot "StartGateway.cmd") -Force

$configDir = Join-Path $env:APPDATA "ArcMapAIAssistant"
New-Item -ItemType Directory -Path $configDir -Force | Out-Null
$installConfig = @{
    install_dir = $targetRoot
    installed_at = (Get-Date).ToString("s")
}
$installConfig | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $configDir "install.json") -Encoding UTF8

$addinId = "{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}"
$addinTargetDir = Join-Path $HOME "Documents\ArcGIS\AddIns\$DesktopVersion\$addinId"
New-Item -ItemType Directory -Path $addinTargetDir -Force | Out-Null
Copy-Item -LiteralPath $addin -Destination (Join-Path $addinTargetDir "arcmapaiassistantaddin.esriaddin") -Force

Write-Host ""
Write-Host "安装完成。"
Write-Host "ArcMap 插件目录：$addinTargetDir"
Write-Host "程序目录：$targetRoot"
Write-Host "配置文件：$(Join-Path $configDir "install.json")"
