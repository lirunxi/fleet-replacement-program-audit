$ErrorActionPreference = "Stop"

$QuartoVersion = "1.10.18"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolsRoot = Join-Path $ProjectRoot ".tools"
$InstallRoot = Join-Path $ToolsRoot "quarto"
$ArchivePath = Join-Path $ToolsRoot "quarto-$QuartoVersion-win.zip"
$ExtractRoot = Join-Path $ToolsRoot "quarto-$QuartoVersion-extract"
$DownloadUrl = "https://github.com/quarto-dev/quarto-cli/releases/download/v$QuartoVersion/quarto-$QuartoVersion-win.zip"

New-Item -ItemType Directory -Force -Path $ToolsRoot | Out-Null

if (Test-Path $ArchivePath) {
    Remove-Item -LiteralPath $ArchivePath -Force
}
if (Test-Path $ExtractRoot) {
    Remove-Item -LiteralPath $ExtractRoot -Recurse -Force
}

Write-Host "Downloading Quarto $QuartoVersion..."
Invoke-WebRequest -Uri $DownloadUrl -OutFile $ArchivePath
Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractRoot -Force

$QuartoCommand = Get-ChildItem -LiteralPath $ExtractRoot -Filter "quarto.cmd" -Recurse |
    Where-Object { $_.FullName -match "[\\/]bin[\\/]quarto\.cmd$" } |
    Select-Object -First 1
if (-not $QuartoCommand) {
    throw "The downloaded archive did not contain bin\quarto.cmd."
}

$ResolvedInstallRoot = Split-Path -Parent (Split-Path -Parent $QuartoCommand.FullName)
if (Test-Path $InstallRoot) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
}
Move-Item -LiteralPath $ResolvedInstallRoot -Destination $InstallRoot
Remove-Item -LiteralPath $ArchivePath -Force
if (Test-Path $ExtractRoot) {
    Remove-Item -LiteralPath $ExtractRoot -Recurse -Force
}

$InstalledVersion = (& (Join-Path $InstallRoot "bin\quarto.cmd") --version).Trim()
if ($InstalledVersion -ne $QuartoVersion) {
    throw "Expected Quarto $QuartoVersion after installation, found $InstalledVersion."
}
Write-Host "Installed project-local Quarto $InstalledVersion."
