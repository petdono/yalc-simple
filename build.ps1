$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv-yarg-lifx\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv-yarg-lifx and install requirements-build.txt first."
}

$workPath = Join-Path $env:TEMP ("YALCS-0.2.0-build-" + [guid]::NewGuid().ToString("N"))
$specPath = Join-Path $env:TEMP ("YALCS-0.2.0-spec-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $specPath | Out-Null
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --collect-submodules bitstring --collect-all tinytuya --collect-all psutil --collect-all tuya_sharing --collect-all qrcode --workpath $workPath --specpath $specPath --name YALCS-0.2.0 (Join-Path $PSScriptRoot "main.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Remove-Item -LiteralPath $workPath -Recurse -Force
Remove-Item -LiteralPath $specPath -Recurse -Force
Write-Host "Executable created: $(Join-Path $PSScriptRoot 'dist\YALCS-0.2.0.exe')"
