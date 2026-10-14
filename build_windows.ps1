param([string]$Version = '1.0.0', [string]$Iscc = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies failed' }
python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Browser download failed' }
$browserExe = python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path);p.stop()"
$browserDirectory = Split-Path -Parent $browserExe
if (-not (Test-Path -LiteralPath $browserExe)) { throw 'Chromium missing' }
python -m PyInstaller --noconfirm --windowed --onedir --name HuoBao --icon assets/app.ico --add-data 'assets;assets' --add-data 'config.example.json;.' --add-data ($browserDirectory + ';browser/chrome-win64') --collect-all playwright --collect-all pystray --collect-data tzdata --exclude-module numpy --exclude-module matplotlib start.pyw
if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
if (-not $Iscc) {
    $candidates = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")
    $Iscc = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw 'Install Inno Setup 6 or specify -Iscc path' }
& $Iscc "/DAppVersion=$Version" installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
