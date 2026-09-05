$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"

if (-not (Test-Path $VenvPath)) {
    py -3.12 -m venv $VenvPath
}

& (Join-Path $VenvPath "Scripts\python.exe") -m pip install --upgrade pip
& (Join-Path $VenvPath "Scripts\python.exe") -m pip install -r (Join-Path $ProjectRoot "requirements.lock")
& (Join-Path $VenvPath "Scripts\python.exe") -m pip install --no-deps --no-build-isolation -e "${ProjectRoot}[dev]"
& (Join-Path $VenvPath "Scripts\python.exe") -m playwright install chromium

$LocalQuarto = Join-Path $ProjectRoot ".tools\quarto\bin\quarto.cmd"
$Quarto = Get-Command quarto -ErrorAction SilentlyContinue
$QuartoPath = if (Test-Path $LocalQuarto) { $LocalQuarto } elseif ($Quarto) { $Quarto.Source } else { $null }
if (-not $QuartoPath) {
    & (Join-Path $PSScriptRoot "install_quarto.ps1")
    $QuartoPath = $LocalQuarto
}

$Version = (& $QuartoPath --version).Trim()
if ($Version -ne "1.10.18") {
    Write-Host "Found Quarto $Version; installing the project-pinned Quarto 1.10.18."
    & (Join-Path $PSScriptRoot "install_quarto.ps1")
    $QuartoPath = $LocalQuarto
    $Version = (& $QuartoPath --version).Trim()
}

Write-Host "Environment ready: Python $(& (Join-Path $VenvPath 'Scripts\python.exe') --version), Quarto $Version"
