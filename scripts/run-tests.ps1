$ErrorActionPreference = 'Stop'
$trainerRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $trainerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { $pythonExe = (Get-Command python).Source }
Set-Location -LiteralPath $trainerRoot
& $pythonExe -m pytest
exit $LASTEXITCODE
