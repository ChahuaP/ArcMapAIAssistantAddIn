# Install the built ArcMap Harness deployment to this machine, replacing the
# previous version. Run after packaging\build_harness.ps1:
#   pwsh -NoProfile -File packaging\install_harness.ps1
# Elevates itself when not admin (UAC prompt). Replaces:
#   C:\Program Files\GeoPilot\harness\            (boundary + composition)
#   C:\Program Files\GeoPilot\OpenAssistantWeb.cmd (Add-in launcher entry)
#   Documents\ArcGIS\AddIns\Desktop10.x\{guid}\    (Add-in package)
#   %LOCALAPPDATA%\ArcMapAIAssistant\dsh-home      (dsh profile, per-user)
param(
    [string]$Stage = ''
)
$ErrorActionPreference = 'Stop'

# -- self-elevation -----------------------------------------------------------
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $repo = Split-Path $PSScriptRoot -Parent
    if (-not $Stage) { $Stage = Join-Path $repo 'build\harness-staging' }
    $logDir = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant\logs'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $log = Join-Path $logDir 'install_harness.log'
    $proc = Start-Process pwsh -Verb RunAs -PassThru -Wait -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
        "& { & '$(Join-Path $PSScriptRoot 'install_harness.ps1')' -Stage '$Stage' *>&1 | Tee-Object -FilePath '$log' }")
    exit $proc.ExitCode
}

$repo = Split-Path $PSScriptRoot -Parent
if (-not $Stage) { $Stage = Join-Path $repo 'build\harness-staging' }
if (-not (Test-Path (Join-Path $Stage 'harness_identity.json'))) {
    throw "staging 缺少 harness_identity.json：$Stage。先运行 packaging\build_harness.ps1。"
}
$TargetRoot = 'C:\Program Files\GeoPilot'
$HarnessDir = Join-Path $TargetRoot 'harness'

# -- stop the running console + boundary --------------------------------------
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -in @('node.exe', 'python.exe')) -and (
        $_.CommandLine -like '*--profile arcmap-harness*' -or
        $_.CommandLine -like '*GeoPilot\harness\server\main.py*')
} | ForEach-Object {
    Write-Output ("stop {0} {1}" -f $_.ProcessId, $_.Name)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 800

# -- replace harness deployment ------------------------------------------------
robocopy (Join-Path $Stage 'harness') $HarnessDir /MIR /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy harness failed: $LASTEXITCODE" }
robocopy (Join-Path $Stage ('harness' + [char]92 + 'arcmap_runtime_py2')) (Join-Path $TargetRoot 'arcmap_runtime_py2') /MIR /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy arcmap_runtime_py2 failed: $LASTEXITCODE" }
robocopy (Join-Path $Stage ('harness' + [char]92 + 'shared_runtime')) (Join-Path $TargetRoot 'shared_runtime') /MIR /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy shared_runtime failed: $LASTEXITCODE" }
robocopy (Join-Path $Stage ('harness' + [char]92 + 'operation_catalog')) (Join-Path $TargetRoot 'operation_catalog') /MIR /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy operation_catalog failed: $LASTEXITCODE" }
Copy-Item (Join-Path $Stage 'OpenAssistantWeb.cmd') (Join-Path $TargetRoot 'OpenAssistantWeb.cmd') -Force
Copy-Item (Join-Path $Stage 'harness_identity.json') (Join-Path $TargetRoot 'harness_identity.json') -Force

# -- replace the ArcMap Add-in packages ---------------------------------------
$addinRoot = Join-Path $env:USERPROFILE 'Documents\ArcGIS\AddIns'
$addinPackage = Join-Path $Stage 'ArcMapAIAssistantAddIn.esriaddin'
if (-not (Test-Path $addinRoot)) { throw "Add-Ins 目录不存在：$addinRoot" }
$placed = 0
Get-ChildItem -LiteralPath $addinRoot -Directory | ForEach-Object {
    Get-ChildItem -LiteralPath $_.FullName -Directory | ForEach-Object {
        $target = Join-Path $_.FullName 'arcmapaiassistantaddin.esriaddin'
        if (Test-Path $target) {
            Copy-Item $addinPackage $target -Force
            $placed++
        }
    }
}
Write-Output "addin packages replaced: $placed"

# -- deploy the per-user dsh profile ------------------------------------------
$AppData = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant'
$DshHome = Join-Path $AppData 'dsh-home'
$ProfileDir = Join-Path $DshHome 'profiles\arcmap-harness'
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

$manifest = @'
{
  "name": "dsh-profile-arcmap-harness",
  "private": true,
  "dependencies": {
    "@deepseek-ai/dsh-mcp-client": "0.1.2-alpha.3"
  },
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-web-app"
      ],
      "patchReload": "live"
    }
  }
}
'@
Set-Content (Join-Path $ProfileDir 'package.json') $manifest -Encoding UTF8

$serverMain = ($HarnessDir + '\server\main.py') -replace '\\', '/'
$patch = @"
# ArcMap Harness composition (installed deployment).
- insert:
    - id: mcp-arcmap
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: arcmap
        transport: stdio
        command: 'C:/Users/user/AppData/Local/Programs/Python/Python311/python.exe'
        args: ['$serverMain']
        toolCallTimeoutMs: 660000
    - id: arcmap-brand
      name: 'arcmap-brand'
    - id: arcmap-status
      name: 'arcmap-status'

- id: ui-brand-official
  disabled: true

- id: code-runtime
  disabled: true

- id: llm-pi-ai
  config:
    providers:
      minimax-cn:
        apiKeyEnv: MINIMAX_API_KEY

- id: agent-default-model
  config:
    provider: minimax-cn
    model: MiniMax-M3

- id: system-prompt
  config:
    persona: >-
      你是 ArcMap Harness——运行在 ArcMap 上的政务 GIS 智能执行助手，由 {{model}}
      模型驱动。你拥有 56 个原生 GIS 工具（如 layer__add_layer 加载图层、
      analysis__buffer 缓冲分析、context__list_layers 列图层、layer__remove_layer
      移除图层等）：工具列表就是完整能力清单，永远不要声称某能力不支持。
      执行前先用 get_boundary_status 确认连接、get_map_context 了解现场；
      工具返回 unresolved 时把 obligations 里的问颉原样向用户澄清，拿到答案后继续。
      写数据/编辑数据类工具执行前要向用户说明影响。你不直接编写或运行 Python，
      也不访问文件系统；一切 GIS 操作必须经工具完成，工具不可用就引导用户打开
      ArcMap 并点击 Add-in 的 ArcMap Harness 按钮。
"@
Set-Content (Join-Path $ProfileDir 'cordis.patch.yml') $patch -Encoding UTF8

# brand plugin: local copy inside dsh-home, referenced by relative file path
$pluginsDir = Join-Path $DshHome 'plugins\arcmap-brand'
New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null
robocopy (Join-Path $Stage 'harness\dsh\plugins\arcmap-brand') $pluginsDir /MIR /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy brand plugin failed: $LASTEXITCODE" }
$statusDir = Join-Path $DshHome 'plugins\arcmap-status'
New-Item -ItemType Directory -Force -Path $statusDir | Out-Null
robocopy (Join-Path $Stage 'harness\dsh\plugins\arcmap-status') $statusDir /MIR /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy status plugin failed: $LASTEXITCODE" }

Push-Location $ProfileDir
try {
    pnpm add "file:../../plugins/arcmap-brand" "file:../../plugins/arcmap-status" 2>&1 | Select-Object -Last 2 | Write-Output
    if ($LASTEXITCODE -ne 0) { throw 'pnpm add plugins failed' }
    pnpm install 2>&1 | Select-Object -Last 2 | Write-Output
    if ($LASTEXITCODE -ne 0) { throw 'pnpm install failed' }
} finally { Pop-Location }

# carry over the provider keys once from the spike home if not present yet
$envFile = Join-Path $DshHome '.env'
if (-not (Test-Path $envFile)) {
    $spikeEnv = Join-Path $HOME '.dsh-spike\.env'
    if (Test-Path $spikeEnv) { Copy-Item $spikeEnv $envFile }
}

# -- report -------------------------------------------------------------------
$identityDoc = Get-Content (Join-Path $TargetRoot 'harness_identity.json') -Raw
Write-Output "INSTALLED: $identityDoc"
Write-Output ("profile: {0}" -f $ProfileDir)
