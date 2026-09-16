# Ollama's OpenAI API does not configure num_ctx. Create a dedicated model
# using its maintained native API, sharing the original weights on disk.
param([switch]$SelectAsDefault)
$ErrorActionPreference = 'Stop'
$endpoint = 'http://127.0.0.1:11434'
$body = @{
    model = 'qwen2.5:7b-arcmap'
    from = 'qwen2.5:7b'
    parameters = @{ num_ctx = 32768; num_predict = 2048; temperature = 0.2 }
    stream = $false
} | ConvertTo-Json -Depth 4
$result = Invoke-RestMethod -Uri "$endpoint/api/create" -Method Post -ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec 120
if ($result.status -ne 'success') { throw "本地 GIS 模型配置失败：$($result | ConvertTo-Json -Compress)" }
$info = Invoke-RestMethod -Uri "$endpoint/api/show" -Method Post -ContentType 'application/json' -Body '{"model":"qwen2.5:7b-arcmap"}' -TimeoutSec 10
if ($info.parameters -notmatch '(?m)^num_ctx\s+32768\s*$' -or $info.capabilities -notcontains 'tools') {
    throw '本地 GIS 模型上下文或工具能力校验失败。'
}
Write-Output '本地 GIS 模型已就绪：qwen2.5:7b-arcmap，实际上下文 32768，最大输出 2048。'
$DshHome = Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant\dsh-home'
New-Item -ItemType Directory -Force -Path $DshHome | Out-Null
$envFile = Join-Path $DshHome '.env'
if (-not (Test-Path $envFile) -or -not (Select-String -LiteralPath $envFile -Pattern '^\s*OLLAMA_API_KEY\s*=\s*\S' -Quiet)) {
    Add-Content -LiteralPath $envFile -Value "`nOLLAMA_API_KEY=ollama" -Encoding UTF8
}
if ($SelectAsDefault) {
    $node = Join-Path $PSScriptRoot 'runtime\node\node.exe'
    $modules = Join-Path $PSScriptRoot 'runtime\dsh\node_modules'
    if (-not (Test-Path $node)) { throw '设置默认模型时请运行安装目录中的 configure_ollama.ps1。' }
    @'
const fs = require('node:fs'), path = require('node:path');
const yaml = require(path.join(process.argv[3], 'yaml'));
const file = path.join(process.argv[2], 'settings.yaml');
const settings = fs.existsSync(file) ? yaml.parse(fs.readFileSync(file, 'utf8')) ?? {} : {};
settings['agent-default-model'] = { provider: 'ollama', model: 'qwen2.5:7b-arcmap' };
fs.writeFileSync(file, yaml.stringify(settings), 'utf8');
'@ | & $node - $DshHome $modules
    if ($LASTEXITCODE -ne 0) { throw '保存默认本地模型失败。' }
}
