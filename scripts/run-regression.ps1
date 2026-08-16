$ErrorActionPreference = 'Stop'
$trainerRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $trainerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { $pythonExe = (Get-Command python).Source }
& $pythonExe (Join-Path $trainerRoot 'tools\run_regression.py') --root $trainerRoot
exit $LASTEXITCODE
