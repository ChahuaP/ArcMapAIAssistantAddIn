# Run against the real local Ollama + ArcMap. The production console must be
# stopped first because the boundary owns the singleton callback port 8765.
param([string]$HarnessDir = 'C:\Program Files\ArcMap Harness\harness')
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue) {
    throw '请先停止控制台服务，释放 8765 回调端口；保留 ArcMap 和 Bridge 运行。'
}
$testRoot = Join-Path $repo ('build\agent-test-' + [guid]::NewGuid().ToString('N'))
$testHome = Join-Path $testRoot 'home'
$stage = Join-Path $testRoot 'stage'
$previous = @{}
foreach ($key in 'DSH_HOME','OLLAMA_API_KEY','DSH_TELEMETRY_MODE','ARCMAP_RUNTIME_MODULES','ARCMAP_ACCEPTANCE_REPORT') {
    $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $stage 'harness') | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'dsh') -Destination (Join-Path $stage 'harness\dsh') -Recurse
    & (Join-Path $repo 'packaging\install_profile.ps1') -Stage $stage -HarnessDir $HarnessDir -DshHome $testHome
    $testPlugin = ([uri](Join-Path $PSScriptRoot 'live_acceptance.mjs')).AbsoluteUri
    Add-Content -LiteralPath (Join-Path $testHome 'profiles\arcmap-harness\cordis.patch.yml') -Encoding UTF8 -Value @"
- insert:
    - id: acceptance-runner
      name: '$testPlugin'
"@
    Set-Content -LiteralPath (Join-Path $testHome 'settings.yaml') -Encoding UTF8 -Value "agent-default-model:`n  provider: ollama`n  model: qwen2.5:7b-arcmap"
    $env:DSH_HOME = $testHome
    $env:OLLAMA_API_KEY = 'ollama'
    $env:DSH_TELEMETRY_MODE = 'DISABLED'
    $env:ARCMAP_RUNTIME_MODULES = Join-Path $HarnessDir 'runtime\dsh\node_modules'
    $env:ARCMAP_ACCEPTANCE_REPORT = Join-Path $repo 'build\agent-acceptance-report.json'
    & (Join-Path $HarnessDir 'runtime\node\node.exe') (Join-Path $env:ARCMAP_RUNTIME_MODULES '@deepseek-ai\dsh\lib\bin.js') --profile arcmap-harness --no-open --port 3181 *> (Join-Path $testRoot 'run.log')
    if ($LASTEXITCODE -ne 0) {
        Get-Content -Encoding UTF8 (Join-Path $testRoot 'run.log') | Select-Object -Last 25
        throw '真实模型验收失败。'
    }
    Get-Content -Encoding UTF8 $env:ARCMAP_ACCEPTANCE_REPORT
} finally {
    foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') }
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath((Join-Path $repo 'build')) + '\agent-test-')) {
        throw "拒绝清理意外路径：$resolved"
    }
    if (Test-Path $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
