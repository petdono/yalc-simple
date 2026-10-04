$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot ".venv-yarg-lifx\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv-yarg-lifx and install requirements-build.txt first."
}

$workPath = Join-Path $env:TEMP ("YARG-LIFX-build-" + [guid]::NewGuid().ToString("N"))
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --collect-submodules bitstring --workpath $workPath --name YARG-LIFX (Join-Path $PSScriptRoot "main.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Remove-Item -LiteralPath $workPath -Recurse -Force
Write-Host "Executable created: $(Join-Path $PSScriptRoot 'dist\YARG-LIFX.exe')"
