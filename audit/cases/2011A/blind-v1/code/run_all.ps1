param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

& (Join-Path $PSScriptRoot 'extract_input.ps1') -Workspace $Workspace
python (Join-Path $PSScriptRoot 'solve.py')
if ($LASTEXITCODE -ne 0) {
    throw "solve.py failed with exit code $LASTEXITCODE"
}

Write-Output 'pass: extraction, analysis, validation and figure generation completed'
