param(
    [string]$DesktopVersion = "Desktop10.1",
    [switch]$RemoveUserConfig,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

function Stop-Gateway {
    $connections = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        $pidValue = $connection.OwningProcess
        if ($pidValue -and $pidValue -ne $PID) {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
        }
    }
}

function Remove-PathIfExists {
    param([string]$Path)
    if ($Path -and (Test-Path -LiteralPath $Path)) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-Host "已删除：$Path"
    }
}

function Remove-InstallDirIfValid {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return
    }
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $rootPath = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.TrimEnd("\") -eq $rootPath.TrimEnd("\")) {
        throw "拒绝删除磁盘根目录：$fullPath"
    }
    $runtimeFile = Join-Path $fullPath "arcmap_runtime_py2\runtime.py"
    $gatewayExe = Join-Path $fullPath "gateway\ArcMapAIAssistantGateway.exe"
    if (-not (Test-Path -LiteralPath $runtimeFile) -or -not (Test-Path -LiteralPath $gatewayExe)) {
        throw "安装目录校验失败，拒绝删除：$fullPath"
    }
    Remove-Item -LiteralPath $fullPath -Recurse -Force
    Write-Host "已删除：$fullPath"
}

function Read-InstallDir {
    $installConfig = Join-Path $env:APPDATA "ArcMapAIAssistant\install.json"
    if (-not (Test-Path -LiteralPath $installConfig)) {
        return ""
    }
    try {
        $payload = Get-Content -Encoding UTF8 -LiteralPath $installConfig -Raw | ConvertFrom-Json
        return [string]$payload.install_dir
    } catch {
        return ""
    }
}

if (-not $Quiet) {
    Write-Host "即将卸载 ArcMap AI Assistant。"
    $answer = Read-Host "是否继续？输入 Y 确认"
    if ($answer -ne "Y" -and $answer -ne "y") {
        Write-Host "已取消。"
        exit 0
    }
}

Stop-Gateway

$addinId = "{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}"
$addinTargetDir = Join-Path $HOME "Documents\ArcGIS\AddIns\$DesktopVersion\$addinId"
Remove-PathIfExists $addinTargetDir

$installDir = Read-InstallDir
Remove-InstallDirIfValid $installDir

$appConfigDir = Join-Path $env:APPDATA "ArcMapAIAssistant"
if ($RemoveUserConfig) {
    Remove-PathIfExists $appConfigDir
} else {
    Remove-PathIfExists (Join-Path $appConfigDir "install.json")
    Write-Host "已保留 API Key 等用户配置。如需彻底删除，请用 -RemoveUserConfig。"
}

Write-Host "卸载完成。"
