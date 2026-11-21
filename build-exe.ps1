$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    python -m PyInstaller --noconfirm EInkTool.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'LICENSE') -Destination (Join-Path $PSScriptRoot 'dist/LICENSE')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'NOTICE') -Destination (Join-Path $PSScriptRoot 'dist/NOTICE')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'enable-acr122-escape.ps1') -Destination (Join-Path $PSScriptRoot 'dist/enable-acr122-escape.ps1')
    Get-Item (Join-Path $PSScriptRoot 'dist/EInkTool.exe')
} finally { Pop-Location }
