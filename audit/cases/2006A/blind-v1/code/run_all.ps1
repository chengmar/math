param(
    [string]$Workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

& (Join-Path $PSScriptRoot 'convert_inputs.ps1') -Workspace $Workspace | Out-Null

python (Join-Path $PSScriptRoot 'solve.py') --workspace $Workspace | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Numerical solve failed' }

python (Join-Path $PSScriptRoot 'verify_outputs.py') --workspace $Workspace
if ($LASTEXITCODE -ne 0) { throw 'Independent output verification failed' }
