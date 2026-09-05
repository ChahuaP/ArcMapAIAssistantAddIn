# ArcMap Harness console launcher: ensure the web console is running and open
# it in the default browser. Called by OpenAssistantWeb.cmd (ArcMap Add-in).
# Requires: dsh on PATH, DSH profile deployed by install_harness.ps1.
param(
    [int]$Port = 3180
)
$ErrorActionPreference = 'Stop'

$AppData = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant'
$DshHome = Join-Path $AppData 'dsh-home'
$LogsDir = Join-Path $AppData 'logs'
$WebLog = Join-Path $LogsDir 'harness_web.log'
$UrlFile = Join-Path $AppData 'harness_url.txt'
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

function Test-PortListening([int]$Number) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Number)
        if ($task.Wait(500)) { return $client.Connected }
        return $false
    } catch { return $false } finally { $client.Dispose() }
}

function Stop-HarnessWeb {
    # Kill whatever owns the console port AND the callback port (8765): the
    # old console's boundary python survives its parent and would keep
    # serving stale /health without /status.
    foreach ($Number in @($Port, 8765)) {
        $lines = netstat -ano -p tcp | Select-String ":$Number\s.*LISTENING"
        foreach ($line in $lines) {
            $parts = ($line.ToString() -replace '\s+', ' ').Trim().Split(' ')
            $procId = $parts[-1]
            if ($procId -match '^\d+$' -and [int]$procId -ne $PID) {
                Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Start-Sleep -Milliseconds 800
    Remove-Item -LiteralPath $UrlFile -Force -ErrorAction SilentlyContinue
}

function Start-HarnessWeb {
    Stop-HarnessWeb
    Remove-Item -LiteralPath $WebLog -Force -ErrorAction SilentlyContinue
    $dsh = Join-Path $env:APPDATA 'npm\dsh.cmd'
    if (-not (Test-Path -LiteralPath $dsh)) { throw "dsh 不存在：$dsh。请先安装 npm 全局 @deepseek-ai/dsh。" }
    $env:DSH_HOME = $DshHome
    Start-Process -FilePath $dsh `
        -ArgumentList @('--profile', 'arcmap-harness', '--no-open', '--port', "$Port") `
        -WindowStyle Hidden -RedirectStandardOutput $WebLog -RedirectStandardError ($WebLog + '.err')
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $WebLog) {
            $match = Select-String -LiteralPath $WebLog -Pattern 'dsh web: (http://\S+)' |
                Select-Object -Last 1
            if ($match) {
                $url = $match.Matches[0].Groups[1].Value
                Set-Content -LiteralPath $UrlFile -Value $url -Encoding UTF8
                return $url
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "ArcMap Harness 控制台启动超时。日志：$WebLog"
}

$portUp = Test-PortListening $Port
$haveUrl = Test-Path -LiteralPath $UrlFile
$url = $null
if ($portUp -and $haveUrl) {
    $url = (Get-Content -LiteralPath $UrlFile -Raw).Trim()
}
if (-not $url) {
    $url = Start-HarnessWeb
}
Start-Process $url
Write-Output "ArcMap Harness: $url"
