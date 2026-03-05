$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Entry = Join-Path $ProjectRoot 'run_miniboard.py'
$Icon = Join-Path $ProjectRoot 'assets\m.ico'
$OutDir = Join-Path $ProjectRoot 'dist_nuitka'

if (-not (Test-Path $Entry)) {
  throw "Entry not found: $Entry"
}
if (-not (Test-Path $Icon)) {
  throw "Icon not found: $Icon"
}

Write-Host "[1/3] Installing build deps (nuitka, ordered-set, zstandard)" -ForegroundColor Cyan
python -m pip install --upgrade nuitka ordered-set zstandard

Write-Host "[2/3] Cleaning output dir" -ForegroundColor Cyan
if (Test-Path $OutDir) {
  Remove-Item -Recurse -Force $OutDir
}

Write-Host "[3/3] Building Miniboard with Nuitka" -ForegroundColor Cyan
python -m nuitka $Entry `
  --standalone `
  --assume-yes-for-downloads `
  --windows-disable-console `
  --enable-plugin=pyside6 `
  --output-dir=$OutDir `
  --output-filename=Miniboard.exe `
  --windows-icon-from-ico=$Icon

Write-Host "" 
Write-Host "Build done." -ForegroundColor Green
Write-Host "Output (folder): $OutDir\run_miniboard.dist" -ForegroundColor Green
Write-Host "Exe: $OutDir\run_miniboard.dist\Miniboard.exe" -ForegroundColor Green
