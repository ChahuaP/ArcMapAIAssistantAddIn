$ErrorActionPreference = 'SilentlyContinue'
$roots = @(
    'C:\Program Files\GeoPilot',
    "$env:APPDATA\ESRI",
    "$env:LOCALAPPDATA\ESRI"
)
$found = @()
foreach ($r in $roots) {
    if (Test-Path -LiteralPath $r) {
        $found += Get-ChildItem -LiteralPath $r -Recurse -Filter 'ArcMapAIAssistant_addin.py' -File -Force | Select-Object -ExpandProperty FullName
    }
}
if ($found.Count -eq 0) {
    "ADDIN_NOT_FOUND::searched " + ($roots -join '; ')
} else {
    foreach ($f in $found) {
        "ADDIN_FILE::$f"
        $hit = Select-String -LiteralPath $f -Pattern 'bind_ui_thread' -SimpleMatch
        if ($hit) {
            foreach ($m in $hit) {
                "bind_ui_thread::FOUND::line$($m.LineNumber)::$($m.Line.Trim())"
            }
        } else {
            "bind_ui_thread::NOT_FOUND"
        }
    }
}
