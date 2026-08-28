param(
    [int]$SensitivityReplicates = 100
)

$ErrorActionPreference = 'Stop'
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$stage = Join-Path $root '_clean-repro-blind-revision'
$reportPath = Join-Path $root 'results\<SOURCE_FILE_REDACTED>'
if (Test-Path -LiteralPath $stage) {
    Write-Host '[FAIL] clean reproduction stage already exists'
    exit 1
}

$rows = @()
$runStatus = 'fail'
$comparisonStatus = 'fail'
$cleanupStatus = 'needs_review'
$stageResolved = $null
try {
    New-Item -ItemType Directory -Path $stage | Out-Null
    $stageResolved = (Resolve-Path -LiteralPath $stage).Path
    if (-not $stageResolved.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $stageResolved -eq $root -or $stageResolved.Contains([IO.Path]::DirectorySeparatorChar + 'blind-v1' + [IO.Path]::DirectorySeparatorChar)) {
        throw "Unsafe stage path: $stageResolved"
    }

    foreach ($name in @('phase-lock.json','allowed-paths.json','forbidden-paths.json','AGENTS.override.md')) {
        Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $stage $name)
    }
    Copy-Item -LiteralPath (Join-Path $root 'input') -Destination $stage -Recurse
    Copy-Item -LiteralPath (Join-Path $root 'code') -Destination $stage -Recurse
    New-Item -ItemType Directory -Path (Join-Path $stage 'paper') | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'paper\main.tex') -Destination (Join-Path $stage 'paper\main.tex')
    Copy-Item -LiteralPath (Join-Path $root 'paper\paper.md') -Destination (Join-Path $stage 'paper\paper.md')

    $preexistingResults = Test-Path -LiteralPath (Join-Path $stage 'results')
    $preexistingFigures = Test-Path -LiteralPath (Join-Path $stage 'figures')
    $preexistingMacros = Test-Path -LiteralPath (Join-Path $stage 'paper\generated-values.tex')
    $preconditionStatus = if (-not $preexistingResults -and -not $preexistingFigures -and -not $preexistingMacros) { 'pass' } else { 'fail' }
    $rows += [pscustomobject]@{
        relative_path = '__empty_output_precondition__'; root_sha256 = ''; clean_sha256 = ''
        status = $preconditionStatus
        detail = "results=$preexistingResults;figures=$preexistingFigures;generated_values=$preexistingMacros"
    }
    if ($preconditionStatus -eq 'fail') { throw 'Clean-output precondition failed.' }

    & pwsh -NoProfile -File (Join-Path $stage 'code\run_all.ps1') -SensitivityReplicates $SensitivityReplicates
    if ($LASTEXITCODE -ne 0) { throw 'Clean run_all failed.' }
    & pwsh -NoProfile -File (Join-Path $stage 'code\validate_outputs.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Clean independent validation failed.' }
    & pwsh -NoProfile -File (Join-Path $stage 'code\crosscheck_search.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Clean dense cross-check failed.' }
    & pwsh -NoProfile -File (Join-Path $stage 'code\check_paper_consistency.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Clean paper consistency failed.' }
    $runStatus = 'pass'

    $deterministicPaths = @((Import-Csv -LiteralPath (Join-Path $root 'results\<SOURCE_FILE_REDACTED>')).relative_path)
    foreach ($relative in $deterministicPaths) {
        $rootPath = Join-Path $root $relative
        $stagePath = Join-Path $stage $relative
        $rootHash = (Get-FileHash -LiteralPath $rootPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $stageHash = (Get-FileHash -LiteralPath $stagePath -Algorithm SHA256).Hash.ToLowerInvariant()
        $rows += [pscustomobject]@{
            relative_path = $relative
            root_sha256 = $rootHash
            clean_sha256 = $stageHash
            status = if ($rootHash -eq $stageHash) { 'pass' } else { 'fail' }
            detail = 'Clean output compared with primary revised output'
        }
    }
    $comparisonStatus = if (@($rows | Where-Object status -eq 'fail').Count -eq 0) { 'pass' } else { 'fail' }
}
finally {
    if ($null -ne $stageResolved -and (Test-Path -LiteralPath $stageResolved)) {
        try {
            $files = @(Get-ChildItem -LiteralPath $stageResolved -Recurse -File)
            foreach ($file in $files) {
                if (-not $file.FullName.StartsWith($stageResolved + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "Unsafe staged file: $($file.FullName)"
                }
                Remove-Item -LiteralPath $file.FullName -Force
            }
            $directories = @(Get-ChildItem -LiteralPath $stageResolved -Recurse -Directory |
                Sort-Object { $_.FullName.Length } -Descending)
            foreach ($directory in $directories) {
                if (-not $directory.FullName.StartsWith($stageResolved + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "Unsafe staged directory: $($directory.FullName)"
                }
                Remove-Item -LiteralPath $directory.FullName -Force
            }
            Remove-Item -LiteralPath $stageResolved -Force
            $cleanupStatus = if (-not (Test-Path -LiteralPath $stageResolved)) { 'pass' } else { 'needs_review' }
        }
        catch {
            $cleanupStatus = 'needs_review'
        }
    }
}

$rows += [pscustomobject]@{
    relative_path = '__clean_run_status__'; root_sha256 = ''; clean_sha256 = ''
    status = $runStatus; detail = 'run_all, independent validation, dense cross-check and paper consistency'
}
$rows += [pscustomobject]@{
    relative_path = '__deterministic_comparison_status__'; root_sha256 = ''; clean_sha256 = ''
    status = $comparisonStatus; detail = 'All deterministic paths from results/<SOURCE_FILE_REDACTED>'
}
$rows += [pscustomobject]@{
    relative_path = '__stage_cleanup_status__'; root_sha256 = ''; clean_sha256 = ''
    status = $cleanupStatus; detail = 'Staged files removed individually after validated path containment'
}
$rows | Export-Csv -LiteralPath $reportPath -NoTypeInformation -Encoding utf8

if ($runStatus -ne 'pass' -or $comparisonStatus -ne 'pass') {
    Write-Host "[FAIL] clean reproduction run=$runStatus comparison=$comparisonStatus cleanup=$cleanupStatus"
    exit 1
}
Write-Host "[PASS] clean reproduction run=$runStatus comparison=$comparisonStatus cleanup=$cleanupStatus"
