# Prepare the fully self-contained runtime that ships inside the installer:
#   runtime\node\node.exe     portable Node
#   runtime\dsh\...           standalone @deepseek-ai/dsh install
#   runtime\python\...        embeddable Python 3.11 + fastmcp/pydantic
#   dsh-profile\...           pre-built (hoisted, symlink-free) dsh profile
# Heavy artifacts are cached under build\runtime-cache so repeated builds are
# fast. Requires network on a cold cache.
param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$cache = Join-Path $repo 'build\runtime-cache'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
$harness = Join-Path $Stage 'harness'
New-Item -ItemType Directory -Force -Path $harness | Out-Null

$dshVersion = '0.1.2-alpha.3'
$pythonVersion = '3.11.9'

# -- Node (single self-contained binary) --------------------------------------
function Get-Node {
    $dst = Join-Path $cache 'node'
    $exe = Join-Path $dst 'node.exe'
    if ($Force -or -not (Test-Path $exe)) {
        $src = (Get-Command node -ErrorAction Stop).Source
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        Copy-Item $src $exe -Force
    }
    return $exe
}

# -- standalone dsh -----------------------------------------------------------
function Get-Dsh {
    $dst = Join-Path $cache 'dsh'
    $bin = Join-Path $dst 'node_modules\@deepseek-ai\dsh\lib\bin.js'
    if ($Force -or -not (Test-Path $bin)) {
        if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        Push-Location $dst
        try {
            npm init -y 2>&1 | Out-Null
            npm install --no-audit --no-fund "@deepseek-ai/dsh@$dshVersion" 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
            if ($LASTEXITCODE -ne 0) { throw "npm install @deepseek-ai/dsh failed: $LASTEXITCODE" }
        } finally { Pop-Location }
    }
    return $dst
}

# -- embeddable Python + boundary dependencies --------------------------------
function Get-Python {
    $dst = Join-Path $cache 'python'
    $exe = Join-Path $dst 'python.exe'
    if ($Force -or -not (Test-Path $exe)) {
        if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        $zip = Join-Path $cache "python-$pythonVersion-embed-amd64.zip"
        if (-not (Test-Path $zip)) {
            $url = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip"
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        }
        Expand-Archive -Path $zip -DestinationPath $dst -Force
        $site = Join-Path $dst 'Lib\site-packages'
        New-Item -ItemType Directory -Force -Path $site | Out-Null
        # Use the build machine's pip to resolve cp311 wheels into the embed.
        python -m pip install --target $site --no-warn-script-location fastmcp==3.1.0 pydantic==2.12.5 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "pip install into embeddable python failed: $LASTEXITCODE" }
        @('python311.zip', '.', 'Lib\site-packages', 'import site') |
            Set-Content (Join-Path $dst 'python311._pth') -Encoding ASCII
        & $exe -c "import fastmcp, pydantic" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'bundled python cannot import fastmcp/pydantic' }
    }
    return $exe
}

# -- pre-built dsh profile (hoisted => no symlinks, safe to copy) -------------
function Get-Profile {
    $dst = Join-Path $cache 'profile'
    $prof = Join-Path $dst 'profiles\arcmap-harness'
    $nm = Join-Path $prof 'node_modules'
    if ($Force -or -not (Test-Path $nm)) {
        if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
        New-Item -ItemType Directory -Force -Path $prof | Out-Null
        # mirror the installed dsh-home layout: <home>\profiles\arcmap-harness
        # with the local plugins at <home>\plugins (referenced as file:..\..\plugins)
        Copy-Item -Recurse (Join-Path $repo 'dsh\plugins\arcmap-brand') (Join-Path $dst 'plugins\arcmap-brand')
        Copy-Item -Recurse (Join-Path $repo 'dsh\plugins\arcmap-status') (Join-Path $dst 'plugins\arcmap-status')
        Copy-Item (Join-Path $repo 'dsh\profile\arcmap-harness\package.json') (Join-Path $prof 'package.json')
        Set-Content (Join-Path $prof '.npmrc') 'node-linker=hoisted' -Encoding ASCII
        $pnpm = (Get-Command pnpm.cmd -ErrorAction SilentlyContinue).Source
        if (-not $pnpm) { $pnpm = (Get-Command pnpm -ErrorAction Stop).Source }
        Push-Location $prof
        try {
            & $pnpm install --no-frozen-lockfile 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
            if ($LASTEXITCODE -ne 0) { throw "pnpm profile install failed: $LASTEXITCODE" }
        } finally { Pop-Location }
    }
    return $dst
}

# -- stage everything ---------------------------------------------------------
$nodeExe = Get-Node
$dshDir = Get-Dsh
$pythonExe = Get-Python
$profileDir = Get-Profile

$rt = Join-Path $harness 'runtime'
robocopy (Split-Path $nodeExe -Parent) (Join-Path $rt 'node') /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
robocopy $dshDir (Join-Path $rt 'dsh') /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
robocopy (Split-Path $pythonExe -Parent) (Join-Path $rt 'python') /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
# profile: ship node_modules + plugins; package.json/cordis are written at install
robocopy (Join-Path $profileDir 'profiles\arcmap-harness\node_modules') (Join-Path $harness 'dsh-profile\node_modules') /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
robocopy (Join-Path $profileDir 'plugins') (Join-Path $harness 'dsh-profile\plugins') /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null

$mb = [math]::Round((Get-ChildItem $rt -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Output "runtime staged: node + dsh + python ($mb MB)"
$global:LASTEXITCODE = 0
