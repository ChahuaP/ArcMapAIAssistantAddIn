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

# -- deploy the per-user dsh profile ------------------------------------------
$AppData = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant'
$DshHome = Join-Path $AppData 'dsh-home'
$ProfileDir = Join-Path $DshHome 'profiles\arcmap-harness'
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

# Pre-installed, hoisted (symlink-free) dependency tree shipped in the package:
# no pnpm / network needed on the target machine.
$profileSource = Join-Path $Stage 'harness\dsh-profile\node_modules'
if (-not (Test-Path -LiteralPath $profileSource)) { throw "staging 缺少 dsh profile：$profileSource" }
$profileNodeModules = Join-Path $ProfileDir 'node_modules'
if (Test-Path -LiteralPath $profileNodeModules) {
    # an older pnpm install left symlinks/junctions robocopy /MIR cannot delete
    cmd /c rmdir /s /q "$profileNodeModules" 2>$null
    if (Test-Path -LiteralPath $profileNodeModules) {
        Remove-Item -LiteralPath $profileNodeModules -Recurse -Force -ErrorAction SilentlyContinue
    }
}
robocopy $profileSource $profileNodeModules /E /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy dsh profile failed: $LASTEXITCODE" }
$LASTEXITCODE = 0

$manifest = @'
{
  "name": "dsh-profile-arcmap-harness",
  "private": true,
  "dependencies": {
    "@deepseek-ai/dsh-mcp-client": "0.1.2-alpha.3",
    "arcmap-brand": "file:..\\..\\plugins\\arcmap-brand",
    "arcmap-status": "file:..\\..\\plugins\\arcmap-status"
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

$serverMain = (Join-Path $HarnessDir 'server\main.py') -replace '\\', '/'
$patch = @"
# ArcMap Harness composition (installed deployment).
- insert:
    - id: mcp-arcmap
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: arcmap
        transport: stdio
        command: '$pythonExe'
        args: [$pythonArgPrefix'$serverMain']
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
      deepseek:
        apiKeyEnv: DEEPSEEK_API_KEY
      qwen:
        displayName: 通义千问
        api: openai-completions
        baseURL: https://dashscope.aliyuncs.com/compatible-mode/v1
        apiKeyEnv: QWEN_API_KEY
        defaultContextWindow: 131072
        defaultMaxTokens: 8192
        models:
          - id: qwen-max
            name: qwen-max
          - id: qwen-plus
            name: qwen-plus
          - id: qwen2.5-7b-instruct
            name: qwen2.5-7b-instruct
      zhipu:
        displayName: 智谱 GLM
        api: openai-completions
        baseURL: https://open.bigmodel.cn/api/paas/v4
        apiKeyEnv: ZHIPU_API_KEY
        defaultContextWindow: 131072
        defaultMaxTokens: 8192
        models:
          - id: glm-4-plus
            name: glm-4-plus
          - id: glm-4-flash
            name: glm-4-flash
      ollama:
        displayName: 本地 Ollama
        api: openai-completions
        baseURL: http://127.0.0.1:11434/v1
        apiKeyEnv: OLLAMA_API_KEY
        defaultContextWindow: 32768
        defaultMaxTokens: 8192
        models:
          - id: qwen2.5:7b
            name: qwen2.5:7b（本地）

# The deployment persona owns the identity; the shipped standard preset also
# registers deployment:persona and collides on a session resume / preset
# re-mount. The `arcmap` preset drops that one persona row.
- id: agent-presets
  config:
    default: arcmap

- id: agent-default-model
  config:
    provider: minimax-cn
    model: MiniMax-M3

- id: system-prompt
  config:
    persona: >-
      你是 ArcMap Harness——运行在 ArcMap 上的政务 GIS 智能执行助手，由 {{model}}
      模型驱动。你拥有 55 个原生 GIS 工具（如 layer__add_layer 加载图层、
      analysis__buffer 缓冲分析、context__list_layers 列图层、layer__remove_layer
      移除图层等）：工具列表就是完整能力清单，永远不要声称某能力不支持。
      执行前先用 get_boundary_status 确认连接、get_map_context 了解现场；
      工具返回 unresolved 时把 obligations 里的问题原样向用户澄清，拿到答案后继续。
      工具返回 failed 时如实报告原因，同一操作失败两次后必须停止重试，向用户说明该操作当前不可用并建议替代方案；绝不隐瞒失败或假装成功。
      写数据/编辑数据类工具执行前要向用户说明影响。你不直接编写或运行 Python，
      也不访问文件系统；一切 GIS 操作必须经工具完成，工具不可用就引导用户打开
      ArcMap 并点击 Add-in 的 ArcMap Harness 按钮。
"@
Set-Content (Join-Path $ProfileDir 'cordis.patch.yml') $patch -Encoding UTF8

# plugins: local copies inside dsh-home, referenced by relative file path
foreach ($plugin in 'arcmap-brand', 'arcmap-status') {
    $pluginDir = Join-Path $DshHome "plugins\$plugin"
    if (Test-Path -LiteralPath $pluginDir) { cmd /c rmdir /s /q "$pluginDir" 2>$null }
    New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null
    robocopy (Join-Path $Stage "harness\dsh-profile\plugins\$plugin") $pluginDir /E /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $plugin plugin failed: $LASTEXITCODE" }
    $LASTEXITCODE = 0
}

# Deploy the shipped `arcmap` agent preset (standard minus its persona row).
$presetDir = Join-Path $DshHome '.agent-presets\arcmap'
if (Test-Path -LiteralPath $presetDir) { Remove-Item -LiteralPath $presetDir -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $presetDir | Out-Null
Copy-Item (Join-Path $Stage 'harness\dsh-profile\presets\arcmap\agent.cordis.yml') (Join-Path $presetDir 'agent.cordis.yml') -Force

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
