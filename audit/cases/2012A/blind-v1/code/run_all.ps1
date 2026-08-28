param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$phaseCheck = '<LAB_ROOT>\.agents\skills\cumcm-a-solve\scripts\check_phase.py'
$python = (Get-Command python -ErrorAction Stop).Source

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "[fail] $Step exited with code $LASTEXITCODE"
    }
    Write-Output "[pass] $Step"
}

& $python $phaseCheck --workspace $workspacePath
Assert-LastExitCode 'phase check'

& ([System.IO.Path]::Combine($workspacePath, 'code', 'extract_xls.ps1')) -Workspace $workspacePath
Assert-LastExitCode 'legacy XLS extraction'

& $python ([System.IO.Path]::Combine($workspacePath, 'code', 'prepare_data.py')) --workspace $workspacePath
Assert-LastExitCode 'data preparation and audit'

& $python ([System.IO.Path]::Combine($workspacePath, 'code', 'analyze.py')) --workspace $workspacePath
Assert-LastExitCode 'models, results, and figures'

$paperDir = [System.IO.Path]::Combine($workspacePath, 'paper')
Push-Location $paperDir
try {
    & xelatex -interaction=nonstopmode -halt-on-error main.tex
    Assert-LastExitCode 'XeLaTeX pass 1'
    & xelatex -interaction=nonstopmode -halt-on-error main.tex
    Assert-LastExitCode 'XeLaTeX pass 2'
}
finally {
    Pop-Location
}

& $python ([System.IO.Path]::Combine($workspacePath, 'code', 'verify.py')) --workspace $workspacePath
Assert-LastExitCode 'artifact verification'

Write-Output '[pass] complete solve-stage rerun'
