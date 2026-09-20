$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Checked {
    & $args[0] $args[1..($args.Count - 1)]
    if ($LASTEXITCODE -ne 0) { throw "Falhou: $($args -join ' ')" }
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Invoke-Checked python -m venv .venv
}
$py = ".venv\Scripts\python.exe"

Invoke-Checked $py -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller==6.22.3
Invoke-Checked $py -m PyInstaller "PDF Tools.spec" --noconfirm

Write-Host "Pronto: dist\PDF Tools.exe"
