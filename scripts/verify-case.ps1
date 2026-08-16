param(
    [Parameter(Mandatory = $true)][string]$CaseId,
    [ValidateSet('blind-v1','blind-final')][string]$Version = 'blind-final'
)

$ErrorActionPreference = 'Stop'
$trainerRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $trainerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { $pythonExe = (Get-Command python).Source }
& $pythonExe (Join-Path $trainerRoot 'tools\verify_frozen.py') --root $trainerRoot --case-id $CaseId --version $Version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonExe (Join-Path $trainerRoot 'tools\verify_case.py') --root $trainerRoot --case-id $CaseId
exit $LASTEXITCODE

