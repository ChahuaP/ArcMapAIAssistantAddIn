param(
    [string]$ReleaseRoot = "",
    [switch]$BuildGateway,
    [switch]$BuildInstaller
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $ReleaseRoot) {
    $ReleaseRoot = Join-Path $repoRoot "release\ArcMapAIAssistant"
}
$ReleaseRoot = [System.IO.Path]::GetFullPath($ReleaseRoot)

function Copy-TreeFiltered {
    param([string]$Source, [string]$Destination)
    $sourceRoot = (Resolve-Path $Source).Path
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force | ForEach-Object {
        if ($_.FullName -match "\\__pycache__(\\|$)") {
            return
        }
        if (-not $_.PSIsContainer -and $_.Extension -eq ".pyc") {
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

function Copy-CmdFile {
    param([string]$Source, [string]$Destination)
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
    $text = [System.IO.File]::ReadAllText($Source, [System.Text.Encoding]::UTF8)
    $text = $text -replace "`r?`n", "`r`n"
    [System.IO.File]::WriteAllText($Destination, $text, $encoding)
}

function Copy-PowerShellFile {
    param([string]$Source, [string]$Destination)
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $true
    $text = [System.IO.File]::ReadAllText($Source, [System.Text.Encoding]::UTF8)
    $text = $text -replace "`r?`n", "`r`n"
    [System.IO.File]::WriteAllText($Destination, $text, $encoding)
}

function Get-AppVersion {
    $appPy = Join-Path $repoRoot "gateway_py3\app.py"
    $text = [System.IO.File]::ReadAllText($appPy, [System.Text.Encoding]::UTF8)
    $match = [regex]::Match($text, 'APP_VERSION\s*=\s*"([^"]+)"')
    if (-not $match.Success) {
        throw "无法从 $appPy 读取 APP_VERSION。"
    }
    return $match.Groups[1].Value
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
    throw "未找到 Inno Setup 编译器 ISCC.exe。请安装 Inno Setup 6 后重试：https://jrsoftware.org/isinfo.php"
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

if ($BuildGateway) {
    Push-Location $PSScriptRoot
    try {
        $distPath = Join-Path $repoRoot "dist"
        $workPath = Join-Path $repoRoot "build"
        Stop-BuildOutputGateway (Join-Path $distPath "ArcMapAIAssistantGateway\ArcMapAIAssistantGateway.exe")
        $pyinstallerArgs = @(".\pyinstaller_gateway.spec", "--noconfirm", "--clean", "--distpath", $distPath, "--workpath", $workPath)
        $pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
        if ($pyinstaller) {
            & $pyinstaller.Source @pyinstallerArgs
        } else {
            python -m PyInstaller @pyinstallerArgs
        }
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
    throw "缺少网关 EXE：$gatewayExe。请先运行：pyinstaller packaging\pyinstaller_gateway.spec --noconfirm --clean，或执行本脚本时加 -BuildGateway。"
}

python (Join-Path $repoRoot "ArcMapAIAssistantAddIn\makeaddin.py") | Out-Host

if (Test-Path -LiteralPath $ReleaseRoot) {
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ReleaseRoot "app") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ReleaseRoot "ArcMapAIAssistantAddIn") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ReleaseRoot "packaging") -Force | Out-Null

$appVersion = Get-AppVersion
Set-Content -LiteralPath (Join-Path $ReleaseRoot "app\VERSION") -Value $appVersion -Encoding ASCII
Copy-TreeFiltered (Join-Path $repoRoot "arcmap_runtime_py2") (Join-Path $ReleaseRoot "app\arcmap_runtime_py2")
Copy-TreeFiltered (Join-Path $repoRoot "operation_catalog") (Join-Path $ReleaseRoot "app\operation_catalog")
Copy-Item -LiteralPath $gatewayDist -Destination (Join-Path $ReleaseRoot "app\gateway") -Recurse -Force
Copy-CmdFile (Join-Path $repoRoot "OpenAssistantWeb.cmd") (Join-Path $ReleaseRoot "app\OpenAssistantWeb.cmd")
Copy-CmdFile (Join-Path $repoRoot "StartGateway.cmd") (Join-Path $ReleaseRoot "app\StartGateway.cmd")
Copy-Item -LiteralPath (Join-Path $repoRoot "gateway_py3\web\help.html") -Destination (Join-Path $ReleaseRoot "app\help.html") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging\uninstall.ico") -Destination (Join-Path $ReleaseRoot "app\uninstall.ico") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin") -Destination (Join-Path $ReleaseRoot "ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin") -Force
Copy-PowerShellFile (Join-Path $repoRoot "packaging\install.ps1") (Join-Path $ReleaseRoot "packaging\install.ps1")
Copy-PowerShellFile (Join-Path $repoRoot "packaging\uninstall.ps1") (Join-Path $ReleaseRoot "packaging\uninstall.ps1")
Copy-CmdFile (Join-Path $repoRoot "InstallArcMapAIAssistant.cmd") (Join-Path $ReleaseRoot "InstallArcMapAIAssistant.cmd")
Copy-CmdFile (Join-Path $repoRoot "UninstallArcMapAIAssistant.cmd") (Join-Path $ReleaseRoot "UninstallArcMapAIAssistant.cmd")
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging\USER_README.txt") -Destination (Join-Path $ReleaseRoot "README.txt") -Force

Write-Host "发布包已生成：$ReleaseRoot"
Write-Host "把整个目录压缩发给用户，用户双击 InstallArcMapAIAssistant.cmd 安装。"

if ($BuildInstaller) {
    $iscc = Find-InnoCompiler
    $setupScript = Join-Path $repoRoot "packaging\GeoPilotSetup.iss"
    $installerOutput = Join-Path $repoRoot "release"
    & $iscc $setupScript "/DMyAppVersion=$appVersion" "/DMySourceDir=$ReleaseRoot" "/DMyOutputDir=$installerOutput"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup 打包失败，退出码：$LASTEXITCODE"
    }
    Write-Host "安装器已生成：$(Join-Path $installerOutput ("GeoPilotSetup-$appVersion.exe"))"
}
