@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dir = Join-Path $env:APPDATA 'ArcMapAIAssistant';" ^
  "New-Item -ItemType Directory -Path $dir -Force | Out-Null;" ^
  "$key = Read-Host 'Paste DeepSeek API key';" ^
  "$obj = [ordered]@{ deepseek_api_key = $key; model = 'deepseek-chat' };" ^
  "$json = $obj | ConvertTo-Json;" ^
  "Set-Content -LiteralPath (Join-Path $dir 'config.json') -Value $json -Encoding UTF8;" ^
  "Write-Host 'Saved config to' (Join-Path $dir 'config.json');" ^
  "Read-Host 'Press Enter to close'"
