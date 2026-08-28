param(
    [switch]$Rerun,
    [int]$SensitivityReplicates = 100
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resultsDir = Join-Path $root 'results'

$deterministicRelativePaths = @(
    'results\key_results.json',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'paper\generated-values.tex',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>'
)

if ($Rerun) {
    $before = [ordered]@{}
    foreach ($relative in $deterministicRelativePaths) {
        $path = Join-Path $root $relative
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing pre-rerun artifact: $relative" }
        $before[$relative] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    & (Join-Path $PSScriptRoot 'run_all.ps1') -SensitivityReplicates $SensitivityReplicates
    if (-not $?) { throw 'run_all.ps1 failed during rerun verification.' }
    $rerunRows = foreach ($relative in $deterministicRelativePaths) {
        $path = Join-Path $root $relative
        $after = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        [pscustomobject]@{
            relative_path = $relative
            before_sha256 = $before[$relative]
            after_sha256 = $after
            status = if ($before[$relative] -eq $after) { 'pass' } else { 'fail' }
        }
    }
    $rerunRows | Export-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>') -NoTypeInformation -Encoding utf8
    if (@($rerunRows | Where-Object status -eq 'fail').Count -gt 0) {
        Write-Host '[FAIL] deterministic rerun hashes differ'
        exit 1
    }
}

& (Join-Path $PSScriptRoot 'validate_outputs.ps1')
if (-not $?) { exit 1 }
& (Join-Path $PSScriptRoot 'crosscheck_search.ps1')
if (-not $?) { exit 1 }
& (Join-Path $PSScriptRoot 'check_paper_consistency.ps1')
if (-not $?) { exit 1 }

$required = @(
    'problem-analysis.md', 'data-audit.md', 'assumptions.yaml', 'variables.yaml',
    'model-selection.md', 'solution-report.yaml', 'reproducibility.yaml',
    'code\SolarShadow.cs', 'code\run_all.ps1', 'code\revision_diagnostics.ps1',
    'code\clean_reproduce.ps1', 'code\<SOURCE_FILE_REDACTED>', 'code\validate_outputs.ps1', 'code\crosscheck_search.ps1',
    'code\check_paper_consistency.ps1', 'code\verify_all.ps1',
    'paper\main.tex', 'paper\paper.md', 'results\key_results.json', 'results\<SOURCE_FILE_REDACTED>'
)
$deliverableRows = foreach ($relative in $required) {
    $path = Join-Path $root $relative
    [pscustomobject]@{
        relative_path = $relative
        status = if ((Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).Length -gt 0) { 'pass' } else { 'fail' }
        bytes = if (Test-Path -LiteralPath $path) { (Get-Item -LiteralPath $path).Length } else { 0 }
        sha256 = if (Test-Path -LiteralPath $path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() } else { '' }
    }
}
$deliverableRows | Export-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>') -NoTypeInformation -Encoding utf8
if (@($deliverableRows | Where-Object status -eq 'fail').Count -gt 0) {
    Write-Host '[FAIL] one or more required deliverables are missing'
    exit 1
}
Write-Host ('[PASS] verify_all completed; rerun={0}' -f $Rerun.IsPresent)
