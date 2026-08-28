param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot),
    [switch]$VerifyPaper
)

$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
foreach ($name in @('results', 'figures', 'paper')) {
    $path = Join-Path $workspacePath $name
    if (-not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType Directory -Path $path | Out-Null
    }
}

& (Join-Path $PSScriptRoot 'extract_data.ps1') -Workspace $workspacePath
if (-not $?) { throw 'Data extraction failed.' }

python (Join-Path $PSScriptRoot 'solve.py') --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw 'Numerical solution failed.' }

if ($VerifyPaper) {
    Push-Location (Join-Path $workspacePath 'paper')
    try {
        & xelatex -interaction=nonstopmode -halt-on-error main.tex | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'First XeLaTeX pass failed.' }
        & xelatex -interaction=nonstopmode -halt-on-error main.tex | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Second XeLaTeX pass failed.' }
    }
    finally {
        Pop-Location
    }
    python (Join-Path $PSScriptRoot 'verify.py') --workspace $workspacePath
    if ($LASTEXITCODE -ne 0) { throw 'Verification failed.' }
}

Write-Output '[pass] solve-stage computation completed'
