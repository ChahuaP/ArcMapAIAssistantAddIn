# Shared deployment of the GIS composition; never reads or overwrites credentials.
param(
    [Parameter(Mandatory)][string]$Stage,
    [Parameter(Mandatory)][string]$HarnessDir,
    [string]$DshHome = (Join-Path $env:LOCALAPPDATA 'ArcMapAIAssistant\dsh-home')
)
$ErrorActionPreference = 'Stop'
$ProfileDir = Join-Path $DshHome 'profiles\arcmap-harness'
$modules = Join-Path $ProfileDir 'node_modules'
function Remove-ProfileEntry([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath($DshHome) + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝删除 profile 外的路径：$resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        $entry = Get-Item -LiteralPath $resolved -Force
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $entry.Delete()
        } else { Remove-Item -LiteralPath $resolved -Recurse -Force }
    }
}

# All dsh imports must resolve to the same runtime module instances.
$scopeTarget = Join-Path $HarnessDir 'runtime\dsh\node_modules\@deepseek-ai'
if (-not (Test-Path -LiteralPath $scopeTarget)) { throw "缺少 dsh 运行时：$scopeTarget" }
Remove-ProfileEntry $modules
New-Item -ItemType Directory -Force -Path $modules | Out-Null
New-Item -ItemType Junction -Path (Join-Path $modules '@deepseek-ai') -Target $scopeTarget | Out-Null

$sourceRoot = Join-Path $Stage 'harness\dsh'
Copy-Item (Join-Path $sourceRoot 'profile\arcmap-harness\package.json') (Join-Path $ProfileDir 'package.json') -Force
$patch = Get-Content -Raw -Encoding UTF8 (Join-Path $sourceRoot 'profile\arcmap-harness\cordis.patch.yml')
$python = (Join-Path $HarnessDir 'runtime\python\python.exe').Replace('\', '/').Replace("'", "''")
$server = (Join-Path $HarnessDir 'server\main.py').Replace('\', '/').Replace("'", "''")
$patch = $patch.Replace("command: 'python'", "command: '$python'").Replace("args: ['-I', '-B', 'server/main.py']", "args: ['-I', '-B', '$server']")
Set-Content -LiteralPath (Join-Path $ProfileDir 'cordis.patch.yml') -Value $patch -Encoding UTF8

foreach ($plugin in 'arcmap-brand', 'arcmap-status', 'arcmap-agent') {
    $pluginDir = Join-Path $DshHome "plugins\$plugin"
    Remove-ProfileEntry $pluginDir
    Copy-Item -LiteralPath (Join-Path $sourceRoot "plugins\$plugin") -Destination $pluginDir -Recurse -Force
    New-Item -ItemType Junction -Path (Join-Path $modules $plugin) -Target $pluginDir | Out-Null
}
Write-Output "GIS profile installed: $ProfileDir"
$global:LASTEXITCODE = 0
