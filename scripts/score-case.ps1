param(
    [Parameter(Mandatory = $true)][string]$CaseId,
    [string]$JudgeScores = ''
)

$ErrorActionPreference = 'Stop'
$trainerRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $trainerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { $pythonExe = (Get-Command python).Source }
$arguments = @((Join-Path $trainerRoot 'tools\score_case.py'), '--root', $trainerRoot, '--case-id', $CaseId)
if ($JudgeScores) { $arguments += @('--judge-scores', $JudgeScores) }
& $pythonExe @arguments
exit $LASTEXITCODE
