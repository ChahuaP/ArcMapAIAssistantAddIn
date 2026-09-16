# Update the console, tool contracts and runtime sources without closing maps.
# Restart ArcMap after installation to reload its Python modules.
param([switch]$NoElevate, [switch]$SelectLocalModel)
$ErrorActionPreference = 'Stop'
if (-not $NoElevate) {
    $identity = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $arguments = @('-NoProfile', '-File', ('"' + $PSCommandPath + '"'), '-NoElevate')
        if ($SelectLocalModel) { $arguments += '-SelectLocalModel' }
        $proc = Start-Process pwsh.exe -Verb RunAs -ArgumentList $arguments -PassThru -Wait
        exit $proc.ExitCode
    }
}
$repo = Split-Path $PSScriptRoot -Parent
$HarnessDir = 'C:\Program Files\ArcMap Harness\harness'
if (-not (Test-Path -LiteralPath (Join-Path $HarnessDir 'runtime\node\node.exe'))) {
    throw '未找到已安装的 ArcMap Harness。请先执行完整安装。'
}
$logDir = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Start-Transcript -Path (Join-Path $logDir 'update_console.log') -Force | Out-Null
try {
    Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -eq (Join-Path $HarnessDir 'runtime\node\node.exe') -and $_.CommandLine -like '*--profile arcmap-harness*') -or
        ($_.ExecutablePath -eq (Join-Path $HarnessDir 'runtime\python\python.exe') -and $_.CommandLine -match '[/\\]server[/\\]main.py')
    } | ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force }
        catch {
            # Stopping the host can make its boundary child exit first.
            if ($_.CategoryInfo.Category -ne [Management.Automation.ErrorCategory]::ObjectNotFound) { throw }
        }
    }
    $stage = Join-Path $repo 'build\console-update'
    New-Item -ItemType Directory -Force -Path (Join-Path $stage 'harness') | Out-Null
    # Mirror only this known application-owned source subtree.
    foreach ($target in @((Join-Path $stage 'harness\dsh'), (Join-Path $HarnessDir 'dsh'))) {
        $absolute = [IO.Path]::GetFullPath($target)
        if ($absolute -notin @([IO.Path]::GetFullPath((Join-Path $stage 'harness\dsh')), [IO.Path]::GetFullPath((Join-Path $HarnessDir 'dsh')))) {
            throw "非法更新路径：$absolute"
        }
        robocopy (Join-Path $repo 'dsh') $absolute /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /XD node_modules | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "控制台源文件复制失败：$LASTEXITCODE" }
    }
    foreach ($script in 'launch_harness.ps1', 'configure_ollama.ps1') {
        Copy-Item (Join-Path $PSScriptRoot "harness\$script") (Join-Path $HarnessDir $script) -Force
    }
    foreach ($directory in 'server', 'shared_runtime', 'operation_catalog', 'arcmap_runtime_py2') {
        $target = [IO.Path]::GetFullPath((Join-Path $HarnessDir $directory))
        if (-not $target.StartsWith([IO.Path]::GetFullPath($HarnessDir) + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "非法运行时更新路径：$target"
        }
        robocopy (Join-Path $repo $directory) $target /E /R:2 /W:1 /NFL /NDL /NJH /NJS /XD __pycache__ /XF *.pyc *.pyo | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "运行时文件复制失败：$directory ($LASTEXITCODE)" }
    }
    & (Join-Path $HarnessDir 'runtime\python\python.exe') -c "import sys; sys.path.insert(0, r'$HarnessDir'); from server.main import mcp; print('Installed tool contracts loaded')"
    if ($LASTEXITCODE -ne 0) { throw '安装后的工具合同加载失败。' }
    & (Join-Path $PSScriptRoot 'install_profile.ps1') -Stage $stage -HarnessDir $HarnessDir
    if ($SelectLocalModel) { & (Join-Path $HarnessDir 'configure_ollama.ps1') -SelectAsDefault }
    $stamp = [ordered]@{
        installed_at = (Get-Date).ToUniversalTime().ToString('o')
        persona_sha256 = (Get-FileHash (Join-Path $HarnessDir 'dsh\plugins\arcmap-agent\lib\persona.txt')).Hash
        profile_sha256 = (Get-FileHash (Join-Path $HarnessDir 'dsh\profile\arcmap-harness\cordis.patch.yml')).Hash
        boundary_main_sha256 = (Get-FileHash (Join-Path $HarnessDir 'server\main.py')).Hash
        boundary_codegen_sha256 = (Get-FileHash (Join-Path $HarnessDir 'server\codegen.py')).Hash
        boundary_precheck_sha256 = (Get-FileHash (Join-Path $HarnessDir 'server\precheck.py')).Hash
        tool_contract_sha256 = (Get-FileHash (Join-Path $HarnessDir 'server\tool_contract.py')).Hash
        runtime_executor_sha256 = (Get-FileHash (Join-Path $HarnessDir 'arcmap_runtime_py2\workflow_executor.py')).Hash
    }
    $stamp | ConvertTo-Json | Set-Content (Join-Path $HarnessDir 'console_identity.json') -Encoding UTF8
    Write-Output '控制台和运行时更新完成。请保存地图并重启 ArcMap，以加载新版 Python 模块。'
} finally { Stop-Transcript | Out-Null }
