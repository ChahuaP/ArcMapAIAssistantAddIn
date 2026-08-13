$ErrorActionPreference = 'Continue'
$log = 'D:\Development\Python\Arcpy\install_run.log'
$ecFile = 'D:\Development\Python\Arcpy\install_exitcode.txt'
Set-Content -LiteralPath $log -Value ('INSTALL_START ' + (Get-Date).ToString('o')) -Encoding UTF8
$code = 0
try {
    & 'D:\Development\Python\Arcpy\build\release_staging\ArcMapAIAssistant\packaging\install.ps1' -InstallDir 'C:\Program Files\GeoPilot' 2>&1 |
        Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE) { $code = $LASTEXITCODE }
    if ($null -eq $code) { $code = 0 }
} catch {
    Add-Content -LiteralPath $log -Value ('INSTALL_EXCEPTION: ' + $_.Exception.Message)
    $code = 99
}
Add-Content -LiteralPath $log -Value ('INSTALL_EXITCODE=' + $code)
Set-Content -LiteralPath $ecFile -Value $code -Encoding UTF8
exit $code
