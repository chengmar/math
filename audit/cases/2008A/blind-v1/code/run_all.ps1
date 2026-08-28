$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Push-Location $workspace
try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File 'code\extract_problem.ps1'
    if ($LASTEXITCODE -ne 0) { throw "Extraction failed with exit code $LASTEXITCODE" }
    & python 'code\solve.py' '--monte-carlo' '100'
    if ($LASTEXITCODE -ne 0) { throw "Numerical pipeline failed with exit code $LASTEXITCODE" }
    & python 'code\check_consistency.py'
    if ($LASTEXITCODE -ne 0) { throw "Consistency check failed with exit code $LASTEXITCODE" }
    Write-Output 'FULL_PIPELINE=pass'
}
finally {
    Pop-Location
}
