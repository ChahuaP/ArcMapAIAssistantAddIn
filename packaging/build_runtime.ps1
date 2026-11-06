# Prepare the fully self-contained runtime that ships inside the installer:
#   runtime\node\node.exe     portable Node
#   runtime\dsh\...           standalone @deepseek-ai/dsh install
#   runtime\python\...        embeddable Python 3.11 + fastmcp/pydantic
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

$dshManifest = Join-Path $PSScriptRoot 'dsh-runtime'
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
    $lock = Join-Path $dshManifest 'package-lock.json'
    $lockHash = (Get-FileHash -LiteralPath $lock).Hash
    $stamp = Join-Path $dst 'installed-lock.sha256'
    if ($Force -or -not (Test-Path $bin) -or -not (Test-Path $stamp) -or
        (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $lockHash) {
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        if (Test-Path -LiteralPath $stamp) { Remove-Item -LiteralPath $stamp }
        Copy-Item -LiteralPath $lock, (Join-Path $dshManifest 'package.json') -Destination $dst -Force
        Push-Location $dst
        try {
            npm ci --engine-strict --ignore-scripts --no-audit --no-fund 2>&1 | ForEach-Object { Write-Host $_ }
            if ($LASTEXITCODE -ne 0) { throw "npm ci for dsh failed: $LASTEXITCODE" }
            & $nodeExe $bin --version | Out-Host
            if ($LASTEXITCODE -ne 0) { throw 'Bundled Node cannot start dsh' }
            Set-Content -LiteralPath $stamp -Value $lockHash -Encoding ASCII
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
        python -m pip install --target $site --no-warn-script-location -r (Join-Path $repo 'requirements.txt') 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "pip install into embeddable python failed: $LASTEXITCODE" }
        @('python311.zip', '.', 'Lib\site-packages', 'import site') |
            Set-Content (Join-Path $dst 'python311._pth') -Encoding ASCII
        & $exe -c "import fastmcp, pydantic" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'bundled python cannot import fastmcp/pydantic' }
    }
    return $exe
}

# -- stage everything ---------------------------------------------------------
$nodeExe = Get-Node
$dshDir = Get-Dsh
$pythonExe = Get-Python

$rt = Join-Path $harness 'runtime'
robocopy (Split-Path $nodeExe -Parent) (Join-Path $rt 'node') /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy Node runtime failed: $LASTEXITCODE" }
robocopy $dshDir (Join-Path $rt 'dsh') /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /XF installed-lock.sha256 | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy dsh runtime failed: $LASTEXITCODE" }
robocopy (Split-Path $pythonExe -Parent) (Join-Path $rt 'python') /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy Python runtime failed: $LASTEXITCODE" }

$mb = [math]::Round((Get-ChildItem $rt -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Output "runtime staged: node + dsh + python ($mb MB)"
$global:LASTEXITCODE = 0
