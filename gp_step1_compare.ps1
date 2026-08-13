$ErrorActionPreference = 'Stop'
$src = 'D:\Development\Python\Arcpy\arcmap_runtime_py2'
$dst = 'C:\Program Files\GeoPilot\arcmap_runtime_py2'

if (-not (Test-Path -LiteralPath $src)) { "SRC_MISSING::$src"; exit 1 }
if (-not (Test-Path -LiteralPath $dst)) { "DST_MISSING::$dst"; exit 1 }

function Get-HashMap($root) {
    $map = @{}
    Get-ChildItem -LiteralPath $root -Recurse -File -Force | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart('\')
        $h = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        $map[$rel.ToLowerInvariant()] = $h
    }
    return $map
}

$srcMap = Get-HashMap $src
$dstMap = Get-HashMap $dst

$miss = New-Object System.Collections.Generic.List[string]
foreach ($k in $srcMap.Keys) {
    if (-not $dstMap.ContainsKey($k)) { $miss.Add("MISSING_IN_DST::$k") }
    elseif ($dstMap[$k] -ne $srcMap[$k]) { $miss.Add("HASH_MISMATCH::$k") }
}
foreach ($k in $dstMap.Keys) {
    if (-not $srcMap.ContainsKey($k)) { $miss.Add("EXTRA_IN_DST::$k") }
}

"source_count=$($srcMap.Count)"
"installed_count=$($dstMap.Count)"
"mismatch_count=$($miss.Count)"
"---MISMATCHES---"
foreach ($m in $miss) { $m }
"---END---"
