param(
    [string]$DesktopVersion = "Desktop10.1"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$addin = Join-Path $repoRoot "ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin"
$addinId = "{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}"
$targetDir = Join-Path $HOME "Documents\ArcGIS\AddIns\$DesktopVersion\$addinId"

if (-not (Test-Path -LiteralPath $addin)) {
    throw "Add-in package not found: $addin"
}

New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
Copy-Item -LiteralPath $addin -Destination (Join-Path $targetDir "arcmapaiassistantaddin.esriaddin") -Force
Write-Host "Installed ArcMap AI Assistant add-in to $targetDir"
