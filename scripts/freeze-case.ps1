param(
    [Parameter(Mandatory = $true)][string]$CaseId,
    [Parameter(Mandatory = $true)][ValidateSet('blind-v1','blind-final')][string]$Version,
    [int]$RandomSeed = 20260816,
    [string]$RunCommand = 'python code/run.py'
)

$ErrorActionPreference = 'Stop'
$trainerRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $trainerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { $pythonExe = (Get-Command python).Source }
& $pythonExe (Join-Path $trainerRoot 'tools\freeze_solution.py') --root $trainerRoot --case-id $CaseId --version $Version --random-seed $RandomSeed --run-command $RunCommand
exit $LASTEXITCODE

