$ErrorActionPreference = "Stop"

Write-Host "OS AI Core Windows UAT check"

$python = Get-Command python -ErrorAction Stop
$version = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$version -lt [version]"3.11") {
    throw "Python 3.11 or newer is required."
}

if (-not (Test-Path ".venv")) {
    & $python.Source -m venv .venv
}
$venvPython = Join-Path ".venv" "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e . -r requirements-dev.txt

$env:OSAI_PROFILE = "local"
$env:OSAI_BIND_HOST = "127.0.0.1"
$env:OSAI_DATABASE_PATH = (Join-Path $PWD "var\osai-uat.sqlite3")
$env:OSAI_SECRET_BACKEND = "test"
$env:OSAI_NETWORK_MODE = "offline"

New-Item -ItemType Directory -Force -Path "var" | Out-Null

& $venvPython -m compileall -q app osai
& $venvPython -m unittest discover -s tests -v
& $venvPython -m osai.runtime check

Write-Host "Windows UAT foundation check PASS"
Write-Host "This check uses the test secret backend and offline readiness only."
Write-Host "Do not treat it as production certification."
