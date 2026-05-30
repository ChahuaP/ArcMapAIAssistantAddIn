param(
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $root "ArcMapBridgeExternal.csproj"

$msbuild = Get-Command MSBuild.exe -ErrorAction SilentlyContinue
if (-not $msbuild) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"),
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\2019\BuildTools\MSBuild\Current\Bin\MSBuild.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $msbuild = [pscustomobject]@{ Source = $candidate }
            break
        }
    }
}
if (-not $msbuild) {
    throw "找不到 MSBuild.exe。"
}

& $msbuild.Source $project "/p:Configuration=$Configuration" "/p:Platform=x86" "/t:Restore;Build" "/v:minimal"
if ($LASTEXITCODE -ne 0) {
    throw "ArcMapBridge.exe 编译失败，退出码：$LASTEXITCODE"
}

$exe = Join-Path $root "bin\$Configuration\ArcMapBridge.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "ArcMapBridge.exe 未生成：$exe"
}
Write-Host $exe
