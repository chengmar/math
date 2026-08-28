param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Push-Location $Workspace
try {
    & (Join-Path $PSScriptRoot 'extract_inputs.ps1') -Workspace $Workspace

    python (Join-Path $PSScriptRoot 'solve.py')
    if ($LASTEXITCODE -ne 0) { throw 'Numerical solution failed' }

    python (Join-Path $PSScriptRoot 'render_paper.py')
    if ($LASTEXITCODE -ne 0) { throw 'Paper rendering failed' }

    python (Join-Path $PSScriptRoot 'verify_outputs.py')
    if ($LASTEXITCODE -ne 0) { throw 'Output verification failed' }

    Write-Output '[PASS] complete solve-stage pipeline'
}
finally {
    Pop-Location
}
