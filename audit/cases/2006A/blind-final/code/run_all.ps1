param(
    [string]$Workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [switch]$ReconvertLegacyInputs
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$env:PYTHONDONTWRITEBYTECODE = '1'

if ($ReconvertLegacyInputs) {
    & (Join-Path $PSScriptRoot 'convert_inputs.ps1') -Workspace $workspacePath | Out-Null
}

python (Join-Path $PSScriptRoot 'check_converted_inputs.py') --workspace $workspacePath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Converted input validation failed' }

python (Join-Path $PSScriptRoot 'solve.py') --workspace $workspacePath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Numerical solve failed' }

python (Join-Path $PSScriptRoot 'check_reproducibility.py') --workspace $workspacePath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Second-run hash comparison failed' }

& (Join-Path $PSScriptRoot 'build_paper.ps1') -Workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw 'Paper build failed or needs review' }

python (Join-Path $PSScriptRoot 'verify_outputs.py') --workspace $workspacePath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Independent output verification failed' }

python (Join-Path $PSScriptRoot 'build_delivery_manifest.py') --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw 'Delivery candidate manifest generation failed' }

python (Join-Path $PSScriptRoot 'check_delivery_manifest.py') --workspace $workspacePath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Delivery candidate manifest check failed' }

Write-Output 'run_all_status=pass'
Write-Output 'freeze_status=needs_review'
