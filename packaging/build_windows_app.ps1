# Build a Windows SheetFlow folder for the x64 PC this script is running on.
# Run from packaging/ or any directory. Requires npm and tar on PATH.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $false
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Triple = "x86_64-pc-windows-msvc"
$App = Join-Path $Root "dist\SheetFlow-windows"
$Cache = Join-Path $Root "packaging\build"
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
if (Test-Path $App) { Remove-Item -Recurse -Force $App }
New-Item -ItemType Directory -Force -Path (Join-Path $App "backend") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $App "frontend") | Out-Null

Write-Host "Building the dashboard"
Push-Location (Join-Path $Root "frontend")
npm install
npm run build
Pop-Location
Copy-Item -Recurse -Force (Join-Path $Root "frontend\dist") (Join-Path $App "frontend\dist")

Write-Host "Downloading a relocatable Python 3.13 for $Triple"
$Headers = @{
  Accept = "application/vnd.github+json"
  "User-Agent" = "sheetflow-build"
}
if ($env:GITHUB_TOKEN) {
  $Headers.Authorization = "Bearer $($env:GITHUB_TOKEN)"
}
$Release = Invoke-RestMethod -Headers $Headers -Uri "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
$Needle = "$Triple-install_only.tar.gz"
$Asset = $Release.assets | Where-Object {
  $_.name.StartsWith("cpython-3.13") -and $_.name.EndsWith($Needle) -and $_.name -notmatch "freethreaded"
} | Select-Object -First 1
if (-not $Asset) {
  throw "No Python 3.13 build was found for $Triple"
}
$Tarball = Join-Path $Cache $Asset.name
if (-not (Test-Path $Tarball)) {
  Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Tarball
}
if (Test-Path (Join-Path $App "python")) { Remove-Item -Recurse -Force (Join-Path $App "python") }
tar -xzf $Tarball -C $App
$Python = Join-Path $App "python\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "backend\requirements.txt")

Write-Host "Copying the application"
robocopy (Join-Path $Root "backend\app") (Join-Path $App "backend\app") /E /XD __pycache__ /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Could not copy the application package." }
robocopy (Join-Path $Root "backend\alembic") (Join-Path $App "backend\alembic") /E /XD __pycache__ /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Could not copy database migrations." }
$global:LASTEXITCODE = 0
Copy-Item (Join-Path $Root "backend\alembic.ini") (Join-Path $App "backend\alembic.ini")
Copy-Item (Join-Path $Root "packaging\launcher.py") (Join-Path $App "launcher.py")

@"
@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%backend"
set "SHEETFLOW_STATIC=%ROOT%frontend\dist"
"%ROOT%python\python.exe" "%ROOT%launcher.py"
"@ | Set-Content -Encoding ascii -Path (Join-Path $App "SheetFlow.cmd")

$Zip = Join-Path $Root "dist\SheetFlow-windows-x64.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path $App -DestinationPath $Zip
Write-Host "Built $App"
Write-Host "Zipped $Zip"
