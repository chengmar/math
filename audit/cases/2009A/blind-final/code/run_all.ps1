param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$expectedCodePath = [System.IO.Path]::GetFullPath((Join-Path $workspacePath 'code'))
$actualCodePath = [System.IO.Path]::GetFullPath($PSScriptRoot)
if (-not $actualCodePath.Equals($expectedCodePath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The entrypoint must be the code/run_all.ps1 inside the requested workspace."
}

foreach ($name in @('results', 'figures', 'paper')) {
    $path = Join-Path $workspacePath $name
    if (-not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType Directory -Path $path | Out-Null
    }
}

# Remove only the explicitly declared products of this pipeline.  Unknown files are
# retained so artifact_manifest.py can fail instead of silently deleting evidence.
$generatedFiles = @(
    'results\<SOURCE_FILE_REDACTED>',
    'results\input-metadata.json',
    'results\metrics.json',
    'results\data-audit.json',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\summary.md',
    'results\run-manifest.json',
    'results\paper-build.json',
    'results\verification.json',
    'results\artifact-manifest.json',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'paper\generated-results.tex',
    'paper\main.aux',
    'paper\main.log',
    'paper\main.out',
    'paper\<SOURCE_FILE_REDACTED>'
)
$workspacePrefix = $workspacePath.TrimEnd('\') + '\'
foreach ($relative in $generatedFiles) {
    $target = [System.IO.Path]::GetFullPath((Join-Path $workspacePath $relative))
    if (-not $target.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Generated target escaped the workspace: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Force
    }
}

$env:PYTHONDONTWRITEBYTECODE = '1'
& (Join-Path $PSScriptRoot 'extract_data.ps1') -Workspace $workspacePath
if (-not $?) { throw 'Data extraction failed.' }

python (Join-Path $PSScriptRoot 'solve.py') --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw 'Numerical solution or property stress generation failed.' }

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

python (Join-Path $PSScriptRoot 'record_build.py') --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw 'Paper build recording failed.' }

python (Join-Path $PSScriptRoot 'verify.py') --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw 'Independent verification failed.' }

python (Join-Path $PSScriptRoot 'artifact_manifest.py') --workspace $workspacePath
if ($LASTEXITCODE -ne 0) { throw 'Generated artifact-set validation failed.' }

Write-Output '[pass] blind-revision computation, paper, verification and artifact manifest completed'
