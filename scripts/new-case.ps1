param(
    [Parameter(Mandatory = $true)][string]$CaseId,
    [Parameter(Mandatory = $true)][ValidateSet('train','dev','exam','dummy')][string]$Split,
    [string]$Title = '',
    [string]$ProblemFamily = 'unspecified'
)

$ErrorActionPreference = 'Stop'
$trainerRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $trainerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { $venvPython = (Get-Command python).Source }
$arguments = @((Join-Path $trainerRoot 'tools\init_case.py'), '--root', $trainerRoot, '--case-id', $CaseId, '--split', $Split, '--problem-family', $ProblemFamily)
if ($Title) { $arguments += @('--title', $Title) }
& $venvPython @arguments
exit $LASTEXITCODE

