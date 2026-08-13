$ErrorActionPreference = 'Stop'
$repoRoot = 'D:\Development\Python\Arcpy'
$srcRuntime = Join-Path $repoRoot 'arcmap_runtime_py2'
$instRuntime = 'C:\Program Files\GeoPilot\arcmap_runtime_py2'

function Test-IsSrcItem([System.IO.FileSystemInfo]$i) {
    if ($i.FullName -match "\\__pycache__(\\|$)") { return $false }
    if (-not $i.PSIsContainer -and $i.Extension -eq '.pyc') { return $false }
    return $true
}

# ---- (a) SHA256 compare repo source vs installed ----
$srcFiles = Get-ChildItem -LiteralPath $srcRuntime -Recurse -File -Force | Where-Object { Test-IsSrcItem $_ }
$srcMap = @{}
foreach ($f in $srcFiles) {
    $rel = $f.FullName.Substring($srcRuntime.Length).TrimStart('\').Replace('\','/')
    $srcMap[$rel] = (Get-FileHash -Algorithm SHA256 -LiteralPath $f.FullName).Hash.ToLowerInvariant()
}
$instFiles = Get-ChildItem -LiteralPath $instRuntime -Recurse -File -Force | Where-Object { Test-IsSrcItem $_ }
$instMap = @{}
foreach ($f in $instFiles) {
    $rel = $f.FullName.Substring($instRuntime.Length).TrimStart('\').Replace('\','/')
    $instMap[$rel] = (Get-FileHash -Algorithm SHA256 -LiteralPath $f.FullName).Hash.ToLowerInvariant()
}

$mismatches = @()
foreach ($k in $srcMap.Keys) {
    if (-not $instMap.ContainsKey($k)) {
        $mismatches += [PSCustomObject]@{ rel=$k; issue='MISSING_IN_INSTALLED' }
    } elseif ($srcMap[$k] -ne $instMap[$k]) {
        $mismatches += [PSCustomObject]@{ rel=$k; issue='HASH_DIFF'; src=$srcMap[$k]; inst=$instMap[$k] }
    }
}
foreach ($k in $instMap.Keys) {
    if (-not $srcMap.ContainsKey($k)) {
        $mismatches += [PSCustomObject]@{ rel=$k; issue='EXTRA_IN_INSTALLED' }
    }
}
"[SHA256_VERIFY]"
"source_dir=$srcRuntime"
"installed_dir=$instRuntime"
"source_files_excluding_pycache_pyc=$($srcMap.Count)"
"installed_files_excluding_pycache_pyc=$($instMap.Count)"
"mismatch_count=$($mismatches.Count)"
if ($mismatches.Count) { $mismatches | Format-Table -AutoSize | Out-String }

# ---- (c) forbidden symbols in installed runtime ----
$forbidden = 'SetTimer','KillTimer','_PENDING_CALLBACK'
$hits = @()
foreach ($f in $instFiles) {
    $txt = [System.IO.File]::ReadAllText($f.FullName)
    foreach ($sym in $forbidden) {
        if ($txt -match [regex]::Escape($sym)) {
            $hits += "$($f.Name) :: $sym"
        }
    }
}
""
"[FORBIDDEN_SYMBOLS_IN_INSTALLED_RUNTIME]"
"forbidden_symbols_checked=$($forbidden -join ', ')"
"hit_count=$($hits.Count)"
if ($hits.Count) { $hits }

# ---- (b) Add-in bind_ui_thread check ----
Add-Type -AssemblyName System.IO.Compression.FileSystem
$addinId = '{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}'
$desktops = 'Desktop10.1','Desktop10.2'
""
"[ADDIN_BIND_UI_THREAD_CHECK]"
foreach ($d in $desktops) {
    $addinPath = Join-Path $env:USERPROFILE "Documents\ArcGIS\AddIns\$d\$addinId\arcmapaiassistantaddin.esriaddin"
    if (-not (Test-Path -LiteralPath $addinPath)) { "  $d : ADDIN_MISSING $addinPath"; continue }
    $zip = [System.IO.Compression.ZipFile]::OpenRead($addinPath)
    try {
        $entry = $zip.Entries | Where-Object { $_.FullName -match 'ArcMapAIAssistant_addin\.py$' } | Select-Object -First 1
        if (-not $entry) { "  $d : addin_py_entry_MISSING"; continue }
        $sr = New-Object System.IO.StreamReader($entry.Open())
        $content = $sr.ReadToEnd(); $sr.Dispose()
        $hasBind = $content -match 'runtime\.bind_ui_thread\(\)'
        "  $d : addin=$addinPath"
        "  $d : addin_py_entry=$($entry.FullName)"
        "  $d : contains_runtime.bind_ui_thread()=$hasBind"
        if (-not $hasBind) {
            # show lines mentioning bind for diagnosis
            ($content -split "`r?`n" | Select-String -Pattern 'bind_ui_thread') | ForEach-Object { "    line: $($_.Line.Trim())" }
        }
    } finally { $zip.Dispose() }
}
"DONE"
