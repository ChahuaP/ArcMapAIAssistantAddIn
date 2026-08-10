param(
    [string]$ReleaseRoot = "",
    [switch]$BuildGateway,
    [switch]$BuildInstaller
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ReleaseRoot) {
    $ReleaseRoot = Join-Path $repoRoot "release"
}
$ReleaseRoot = [System.IO.Path]::GetFullPath($ReleaseRoot)
$stageRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "build\release_staging\ArcMapAIAssistant"))

function Assert-UnderRepo {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($repoRoot)
    if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝操作仓库外路径：$full"
    }
}

Assert-UnderRepo $ReleaseRoot
Assert-UnderRepo $stageRoot

function Test-IsPackageSourceItem {
    param([System.IO.FileSystemInfo]$Item)
    if ($Item.FullName -match "\\__pycache__(\\|$)") {
        return $false
    }
    if (-not $Item.PSIsContainer -and $Item.Extension -eq ".pyc") {
        return $false
    }
    return $true
}

function Copy-TreeFiltered {
    param([string]$Source, [string]$Destination)
    $sourceRoot = (Resolve-Path $Source).Path
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force | ForEach-Object {
        if (-not (Test-IsPackageSourceItem $_)) {
            return
        }
        $relative = $_.FullName.Substring($sourceRoot.Length).TrimStart("\")
        $target = Join-Path $Destination $relative
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Path $target -Force | Out-Null
        } else {
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
}

function Write-TextFile {
    param([string]$Path, [string]$Text, [bool]$Bom)
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $Bom
    $text = $Text -replace "`r?`n", "`r`n"
    [System.IO.File]::WriteAllText($Path, $text, $encoding)
}

function Copy-PowerShellFile {
    param([string]$Source, [string]$Destination)
    $text = [System.IO.File]::ReadAllText($Source, [System.Text.Encoding]::UTF8)
    Write-TextFile $Destination $text $true
}

function Write-AppCommandFiles {
    param([string]$AppRoot)
    $openCommand = @'
@echo off
setlocal
cd /d "%~dp0"
start "" "http://127.0.0.1:8765"
exit /b 0
'@
    $startCommand = @'
@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0gateway\ArcMapAIAssistantGateway.exe" (
  echo Missing gateway executable.
  pause
  exit /b 1
)
start "" "%~dp0gateway\ArcMapAIAssistantGateway.exe"
exit /b 0
'@
    Write-TextFile (Join-Path $AppRoot "OpenAssistantWeb.cmd") $openCommand $false
    Write-TextFile (Join-Path $AppRoot "StartGateway.cmd") $startCommand $false
}

function Get-AppVersion {
    $versionPath = Join-Path $repoRoot "VERSION"
    $version = [System.IO.File]::ReadAllText($versionPath, [System.Text.Encoding]::ASCII).Trim()
    if ($version -notmatch '^\d+(\.\d+)+$') {
        throw "$versionPath 不含有效版本号。"
    }
    return $version
}

function Find-InnoCompiler {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidates = @()
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    }
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    }
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw "未找到 Inno Setup 编译器 ISCC.exe。请安装 Inno Setup 6。"
}

function Build-ExternalArcMapBridge {
    $buildScript = Join-Path $repoRoot "ArcMapBridgeExternal\build.ps1"
    & $buildScript | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "ArcMapBridge.exe 编译失败，退出码：$LASTEXITCODE"
    }
    $exe = Join-Path $repoRoot "ArcMapBridgeExternal\bin\Release\ArcMapBridge.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "缺少 ArcMapBridge.exe：$exe"
    }
    $identity = Join-Path (Split-Path -Parent $exe) "ArcMapBridge.build"
    if (-not (Test-Path -LiteralPath $identity)) {
        throw "缺少 ArcMapBridge.build：$identity"
    }
    $jsonAssembly = Join-Path (Split-Path -Parent $exe) "Newtonsoft.Json.dll"
    if (-not (Test-Path -LiteralPath $jsonAssembly)) {
        throw "缺少 ArcMapBridge 运行依赖：$jsonAssembly"
    }
    return $exe
}

function Build-ArcMapAddIn {
    $addin = Join-Path $repoRoot "ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin"
    if (Test-Path -LiteralPath $addin) {
        Assert-UnderRepo $addin
        Remove-Item -LiteralPath $addin -Force
    }
    python (Join-Path $repoRoot "ArcMapAIAssistantAddIn\makeaddin.py") | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "ArcMap Add-in 打包失败，退出码：$LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $addin)) {
        throw "缺少 ArcMap Add-in 包：$addin"
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($addin)
    try {
        if (-not ($archive.Entries | Where-Object { $_.FullName -eq "config.xml" })) {
            throw "ArcMap Add-in 包缺少 config.xml：$addin"
        }
    }
    finally {
        $archive.Dispose()
    }
    return $addin
}

function Clear-GeneratedBuildOutputs {
    foreach ($path in @(
        (Join-Path $repoRoot "ArcMapBridgeExternal\bin"),
        (Join-Path $repoRoot "ArcMapBridgeExternal\obj"),
        (Join-Path $repoRoot "ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin")
    )) {
        if (Test-Path -LiteralPath $path) {
            Assert-UnderRepo $path
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

function Stop-BuildOutputGateway {
    param([string]$GatewayExePath)
    $target = [System.IO.Path]::GetFullPath($GatewayExePath)
    Get-Process -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            if ($_.Path -and ([System.IO.Path]::GetFullPath($_.Path) -eq $target)) {
                Stop-Process -Id $_.Id -Force -ErrorAction Stop
            }
        } catch {
        }
    }
}

function New-PackagingPython {
    $venvRoot = Join-Path $repoRoot "build\packaging-venv"
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    if (Test-Path -LiteralPath $venvRoot) {
        Assert-UnderRepo $venvRoot
        Remove-Item -LiteralPath $venvRoot -Recurse -Force
    }
    python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "创建隔离打包环境失败。"
    }
    & $venvPython -m pip install --disable-pip-version-check --requirement (Join-Path $PSScriptRoot "requirements.txt") | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "安装隔离打包依赖失败，退出码：$LASTEXITCODE"
    }
    return $venvPython
}

if ($BuildGateway) {
    Push-Location $PSScriptRoot
    try {
        $distPath = Join-Path $repoRoot "dist"
        $workPath = Join-Path $repoRoot "build"
        $packagePython = New-PackagingPython
        Stop-BuildOutputGateway (Join-Path $distPath "ArcMapAIAssistantGateway\ArcMapAIAssistantGateway.exe")
        $pyinstallerArgs = @(".\pyinstaller_gateway.spec", "--noconfirm", "--clean", "--distpath", $distPath, "--workpath", $workPath)
        & $packagePython -m PyInstaller @pyinstallerArgs
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller 打包失败，退出码：$LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

$gatewayDist = Join-Path $repoRoot "dist\ArcMapAIAssistantGateway"
$gatewayExe = Join-Path $gatewayDist "ArcMapAIAssistantGateway.exe"
if (-not (Test-Path -LiteralPath $gatewayExe)) {
    throw "缺少网关 EXE：$gatewayExe。请执行 packaging\build_release.ps1 -BuildGateway -BuildInstaller。"
}

try {
$addinPackage = Build-ArcMapAddIn
$externalBridgeExe = Build-ExternalArcMapBridge

if (Test-Path -LiteralPath $stageRoot) {
    Assert-UnderRepo $stageRoot
    Remove-Item -LiteralPath $stageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stageRoot "app") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stageRoot "app\bridge") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stageRoot "ArcMapAIAssistantAddIn") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stageRoot "packaging") -Force | Out-Null

$appVersion = Get-AppVersion
Copy-Item -LiteralPath (Join-Path $repoRoot "VERSION") -Destination (Join-Path $stageRoot "app\VERSION") -Force
Copy-TreeFiltered (Join-Path $repoRoot "arcmap_runtime_py2") (Join-Path $stageRoot "app\arcmap_runtime_py2")
Copy-TreeFiltered (Join-Path $repoRoot "shared_runtime") (Join-Path $stageRoot "app\shared_runtime")
Copy-TreeFiltered (Join-Path $repoRoot "operation_catalog") (Join-Path $stageRoot "app\operation_catalog")
Copy-Item -LiteralPath $gatewayDist -Destination (Join-Path $stageRoot "app\gateway") -Recurse -Force
Write-AppCommandFiles (Join-Path $stageRoot "app")
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging\uninstall.ico") -Destination (Join-Path $stageRoot "app\uninstall.ico") -Force
Copy-Item -LiteralPath $externalBridgeExe -Destination (Join-Path $stageRoot "app\bridge\ArcMapBridge.exe") -Force
Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $externalBridgeExe) "ArcMapBridge.build") -Destination (Join-Path $stageRoot "app\bridge\ArcMapBridge.build") -Force
Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $externalBridgeExe) "Newtonsoft.Json.dll") -Destination (Join-Path $stageRoot "app\bridge\Newtonsoft.Json.dll") -Force
$identitySources = @(
    (Join-Path $repoRoot "VERSION"),
    (Join-Path $repoRoot "shared_runtime"),
    (Join-Path $repoRoot "operation_catalog"),
    (Join-Path $repoRoot "gateway_py3"),
    (Join-Path $repoRoot "arcmap_runtime_py2"),
    (Join-Path $repoRoot "ArcMapBridgeExternal\Program.cs"),
    (Join-Path $repoRoot "ArcMapBridgeExternal\ArcMapBridgeExternal.csproj")
)
$identityBytes = [System.Text.StringBuilder]::new()
foreach ($source in $identitySources) {
    if (Test-Path -LiteralPath $source -PathType Container) {
        Get-ChildItem -LiteralPath $source -File -Recurse -Force |
            Where-Object { Test-IsPackageSourceItem $_ } |
            Sort-Object FullName |
            ForEach-Object {
            [void]$identityBytes.Append($_.FullName.Substring($repoRoot.Length).Replace('\','/'))
            [void]$identityBytes.Append(':')
            [void]$identityBytes.Append((Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant())
            [void]$identityBytes.Append("`n")
        }
    } else {
        [void]$identityBytes.Append((Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant())
        [void]$identityBytes.Append("`n")
    }
}
$identityHash = [BitConverter]::ToString(([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($identityBytes.ToString())))).Replace('-','').ToLowerInvariant()
$identityJson = @{ deployment_hash = $identityHash } | ConvertTo-Json -Compress
foreach ($component in @('gateway','bridge','arcmap_runtime_py2')) {
    Set-Content -LiteralPath (Join-Path $stageRoot "app\$component\deployment_identity.json") -Value $identityJson -Encoding UTF8
}
Copy-Item -LiteralPath $addinPackage -Destination (Join-Path $stageRoot "ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin") -Force
Copy-PowerShellFile (Join-Path $repoRoot "packaging\install.ps1") (Join-Path $stageRoot "packaging\install.ps1")
Copy-PowerShellFile (Join-Path $repoRoot "packaging\uninstall.ps1") (Join-Path $stageRoot "packaging\uninstall.ps1")

if (Test-Path -LiteralPath $ReleaseRoot) {
    Assert-UnderRepo $ReleaseRoot
    Get-ChildItem -LiteralPath $ReleaseRoot -Force | Remove-Item -Recurse -Force
} else {
    New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
}
Copy-TreeFiltered (Join-Path $repoRoot "agent_integrations\geopilot-arcmap") (Join-Path $ReleaseRoot "geopilot-arcmap")

if ($BuildInstaller) {
    $iscc = Find-InnoCompiler
    $setupScript = Join-Path $repoRoot "packaging\GeoPilotSetup.iss"
    & $iscc $setupScript "/DMyAppVersion=$appVersion" "/DMySourceDir=$stageRoot" "/DMyOutputDir=$ReleaseRoot"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup 打包失败，退出码：$LASTEXITCODE"
    }
    Write-Host "安装器已生成：$(Join-Path $ReleaseRoot ("GeoPilotSetup-$appVersion.exe"))"
}
} finally {
    Clear-GeneratedBuildOutputs
}

Write-Host "release 已生成：$ReleaseRoot"
Write-Host "交付内容：GeoPilotSetup-$appVersion.exe 和 geopilot-arcmap skill。"
