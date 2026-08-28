[CmdletBinding()]
param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$paperPath = Join-Path $workspacePath 'paper'
$engine = Get-Command xelatex -ErrorAction SilentlyContinue
if ($null -eq $engine) {
    Write-Output 'paper_build_status=needs_review'
    Write-Output 'reason=xelatex unavailable'
    exit 0
}

Push-Location $paperPath
try {
    for ($run = 1; $run -le 2; $run++) {
        & $engine.Source -interaction=nonstopmode -halt-on-error -file-line-error 'main.tex'
        if ($LASTEXITCODE -ne 0) {
            throw "XeLaTeX run $run failed with exit code $LASTEXITCODE."
        }
    }
}
finally {
    Pop-Location
}

$pdf = Join-Path $paperPath '<SOURCE_FILE_REDACTED>'
if (-not (Test-Path -LiteralPath $pdf -PathType Leaf)) {
    throw '<SOURCE_FILE_REDACTED> is absent.'
}
Write-Output 'paper_build_status=pass'
Write-Output "pdf=$pdf"
