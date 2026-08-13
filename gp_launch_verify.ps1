$ErrorActionPreference = 'SilentlyContinue'

function Get-ProcByName($n) { Get-Process -Name $n -ErrorAction SilentlyContinue }

# ---------- ArcMap ----------
$arcmapExe = 'C:\Program Files (x86)\ArcGIS\Desktop10.2\bin\ArcMap.exe'
$am = Get-ProcByName 'ArcMap' | Select-Object -First 1
if ($am) { $amState = 'already-running' }
else { $am = Start-Process -FilePath $arcmapExe -PassThru; $amState = 'started' }

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    $cur = Get-Process -Id $am.Id -ErrorAction SilentlyContinue
    if ($cur -and $cur.MainWindowHandle -ne [IntPtr]::Zero -and $cur.Responding) { $am = $cur; break }
    Start-Sleep -Milliseconds 600
}
$amFinal = Get-Process -Id $am.Id -ErrorAction SilentlyContinue
"ARCMAP_PID=$($amFinal.Id)"
"ARCMAP_STATE=$amState"
"ARCMAP_HWND=$($amFinal.MainWindowHandle)"
"ARCMAP_RESPONDING=$($amFinal.Responding)"

# ---------- Bridge ----------
$bridgeExe = 'C:\Program Files\GeoPilot\bridge\ArcMapBridge.exe'
$b = Get-ProcByName 'ArcMapBridge' | Select-Object -First 1
if ($b) { $bState = 'already-running' }
else { $b = Start-Process -FilePath $bridgeExe -WindowStyle Hidden -PassThru; $bState = 'started' }
"BRIDGE_PID=$($b.Id)"
"BRIDGE_PATH=$bridgeExe"
"BRIDGE_STATE=$bState"

# ---------- Gateway ----------
$gwExe = 'C:\Program Files\GeoPilot\gateway\ArcMapAIAssistantGateway.exe'
$g = Get-ProcByName 'ArcMapAIAssistantGateway' | Select-Object -First 1
if ($g) { $gState = 'already-running' }
else { $g = Start-Process -FilePath $gwExe -WindowStyle Hidden -PassThru; $gState = 'started' }
"GATEWAY_PID=$($g.Id)"
"GATEWAY_PATH=$gwExe"
"GATEWAY_STATE=$gState"

# ---------- Health ----------
Start-Sleep -Seconds 1
$dl = (Get-Date).AddSeconds(30)
$h66 = $false; $h65 = $false
$body66 = ''; $body65 = ''
while ((Get-Date) -lt $dl) {
    if (-not $h66) {
        try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8766/health' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { $h66 = $true; $body66 = $r.Content } } catch {}
    }
    if (-not $h65) {
        try { $r2 = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/health' -UseBasicParsing -TimeoutSec 3; if ($r2.StatusCode -eq 200) { $h65 = $true; $body65 = $r2.Content } } catch {}
    }
    if ($h66 -and $h65) { break }
    Start-Sleep -Milliseconds 600
}
$s66 = if ($h66) { 200 } else { 0 }
$s65 = if ($h65) { 200 } else { 0 }
"HEALTH_8766_STATUS=$s66"
"HEALTH_8766_BODY=$body66"
"HEALTH_8765_STATUS=$s65"
"HEALTH_8765_BODY=$body65"

foreach ($pair in @(@('8766',$body66),@('8765',$body65))) {
    $port = $pair[0]; $bd = $pair[1]
    try {
        $j = $bd | ConvertFrom-Json -ErrorAction Stop
        $props = $j.PSObject.Properties | Where-Object { $_.Name -match 'hash' }
        foreach ($p in $props) { "DEPLOY_HASH_$port" + "_$($p.Name)=$($p.Value)" }
    } catch {}
}
"DONE"
