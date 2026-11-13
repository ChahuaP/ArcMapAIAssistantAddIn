param(
    [string]$Configuration = "Release",
    [Parameter(Mandatory)][string]$BuildRoot
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $root
. (Join-Path $repoRoot 'packaging\build_paths.ps1')
$BuildRoot = Assert-ExternalBuildPath $BuildRoot $repoRoot
$project = Join-Path $root "ArcMapBridgeExternal.csproj"
$versionPath = Join-Path $repoRoot "VERSION"
$version = [System.IO.File]::ReadAllText($versionPath, [System.Text.Encoding]::ASCII).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "$versionPath 必须包含三段式版本号。"
}
$intermediate = Join-Path $BuildRoot 'obj'
$generatedDir = Join-Path $BuildRoot 'generated'
$output = Join-Path $BuildRoot "bin\$Configuration"
New-Item -ItemType Directory -Path $generatedDir -Force | Out-Null
[System.IO.File]::WriteAllLines(
    (Join-Path $generatedDir "AssemblyVersion.g.cs"),
    @(
        "using System.Reflection;",
        "[assembly: AssemblyVersion(`"$version`")]",
        "[assembly: AssemblyFileVersion(`"$version`")]"
    ),
    (New-Object System.Text.UTF8Encoding($false))
)

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

& $msbuild.Source $project "/p:Configuration=$Configuration" "/p:Platform=x86" "/p:BaseIntermediateOutputPath=$intermediate\" "/p:MSBuildProjectExtensionsPath=$intermediate\" "/p:OutputPath=$output\" "/p:GeneratedVersionFile=$generatedDir\AssemblyVersion.g.cs" "/p:RestorePackagesPath=$BuildRoot\nuget" "/p:RestorePackagesWithLockFile=true" "/p:NuGetLockFilePath=$root\packages.lock.json" "/p:RestoreLockedMode=true" "/t:Restore;Build" "/v:minimal"
if ($LASTEXITCODE -ne 0) {
    throw "ArcMapBridge.exe 编译失败，退出码：$LASTEXITCODE"
}

$exe = Join-Path $output 'ArcMapBridge.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw "ArcMapBridge.exe 未生成：$exe"
}
$sourceHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes((@('Program.cs', 'Mxd.cs') | ForEach-Object {
        (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $root $_)).Hash.ToLowerInvariant()
    }) -join ':'))).ToLowerInvariant()
$binaryHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash.ToLowerInvariant()
$identity = Join-Path (Split-Path -Parent $exe) "ArcMapBridge.build"
[System.IO.File]::WriteAllLines(
    $identity,
    @("source_sha256=$sourceHash", "binary_sha256=$binaryHash"),
    [System.Text.Encoding]::ASCII
)
Write-Host $exe
Write-Host $identity
