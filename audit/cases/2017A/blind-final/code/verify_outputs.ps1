param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot),
    [switch]$GeneratedOnly
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = [System.IO.Path]::GetFullPath($Workspace)
$workspacePrefix = $workspaceRoot.TrimEnd('\') + '\'
$failures = [System.Collections.Generic.List[string]]::new()

function In-Workspace {
    param([string]$RelativePath)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot $RelativePath))
    if (-not $candidate.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes workspace: $candidate"
    }
    return $candidate
}

$required = @(
    '<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>', 'results\<SOURCE_FILE_REDACTED>',
    'results\calibration.json', 'results\<SOURCE_FILE_REDACTED>',
    'results\direction-publication-verification.json',
    'results\angle-step-validation.json',
    'results\model-comparison.json',
    'results\sirt-iteration-curves.json', 'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>', 'results\sample-values.json',
    'results\problem2-geometry.json', 'results\problem3-geometry.json',
    'results\problem3-threshold-sensitivity.json',
    'results\stability.json', 'results\<SOURCE_FILE_REDACTED>',
    'results\input-provenance.json', 'results\numerical-summary.json',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'paper\generated-values.tex'
)
foreach ($name in @('problem2', 'problem3')) {
    foreach ($iterations in @(0, 5, 10, 20, 40, 80, 160)) {
        $required += "results\exploratory\$name-sirt$<SOURCE_FILE_REDACTED>"
        $required += "figures\exploratory\$name-sirt$<SOURCE_FILE_REDACTED>"
    }
}
if (-not $GeneratedOnly) {
    $required += @(
        'problem-analysis.md', 'data-audit.md', 'assumptions.yaml',
        'variables.yaml', 'model-selection.md', 'solution-report.yaml',
        'reproducibility.yaml', 'paper\main.tex', 'paper\paper.md',
        'run_all.ps1', 'run_reproduction.ps1',
        'results\input-identity-comparison.json',
        'results\reproduction-report.json',
        'results\paper-consistency.json'
    )
}
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (In-Workspace $_) -PathType Leaf) })
if ($missing.Count -gt 0) {
    $failures.Add("Missing required files: $($missing -join ', ')")
}

$jsonFiles = @(
    'results\calibration.json',
    'results\direction-publication-verification.json',
    'results\angle-step-validation.json',
    'results\model-comparison.json',
    'results\sirt-iteration-curves.json',
    'results\sample-values.json',
    'results\problem2-geometry.json',
    'results\problem3-geometry.json',
    'results\problem3-threshold-sensitivity.json',
    'results\stability.json',
    'results\input-provenance.json',
    'results\numerical-summary.json'
)
if (-not $GeneratedOnly) {
    $jsonFiles += @(
        'results\input-identity-comparison.json',
        'results\reproduction-report.json',
        'results\paper-consistency.json'
    )
}
$jsonStatus = @()
foreach ($relative in $jsonFiles) {
    try {
        $document = Get-Content -LiteralPath (In-Workspace $relative) -Raw -Encoding UTF8 | ConvertFrom-Json
        $status = [string]$document.status
        if ($status -notin @('pass', 'fail', 'needs_review')) {
            $failures.Add("Invalid status in ${relative}: $status")
        }
        if ($status -eq 'fail') {
            $failures.Add("Generated JSON reports fail: $relative")
        }
        $jsonStatus += [PSCustomObject]@{ file = $relative; status = $status }
    }
    catch {
        $failures.Add("Invalid JSON ${relative}: $($_.Exception.Message)")
    }
}

try {
    $directionVerification = Get-Content -LiteralPath `
        (In-Workspace 'results\direction-publication-verification.json') -Raw | ConvertFrom-Json
    if ([string]$directionVerification.status -ne 'pass') {
        $failures.Add('Direction publication verification is not pass.')
    }
    if ([double]$directionVerification.nrmse_difference -ge 1e-12) {
        $failures.Add('Published direction NRMSE differs from the internal convention.')
    }
    $directions = Import-Csv -LiteralPath (In-Workspace 'results\<SOURCE_FILE_REDACTED>')
    if ($directions.Count -ne 180) { $failures.Add("Direction count is $($directions.Count), expected 180") }
    if (@($directions | Where-Object { $_.reverse_detector_index_if_normalized -eq 'true' }).Count -ne 29) {
        $failures.Add('<SOURCE_FILE_REDACTED>.')
    }
    for ($index = 1; $index -lt $directions.Count; $index++) {
        $increment = [double]$directions[$index].signed_detector_normal_degrees -
            [double]$directions[$index - 1].signed_detector_normal_degrees
        if ([Math]::Abs($increment - 1.0) -gt 1e-8) {
            $failures.Add("Signed direction increment fails at row $($index + 1).")
            break
        }
    }
}
catch { $failures.Add("Direction semantic check failed: $($_.Exception.Message)") }

try {
    $angleValidation = Get-Content -LiteralPath `
        (In-Workspace 'results\angle-step-validation.json') -Raw | ConvertFrom-Json
    if ([string]$angleValidation.leakage_check -ne 'pass') {
        $failures.Add('Angle-step fit/evaluate separation is not pass.')
    }
    if ($angleValidation.development_folds.Count -ne 5 -or
        $angleValidation.final_holdout_indices.Count -ne 30) {
        $failures.Add('Angle-step blocked-fold or final-holdout structure is incomplete.')
    }
}
catch { $failures.Add("Angle-step validation check failed: $($_.Exception.Message)") }

try {
    $sirt = Get-Content -LiteralPath (In-Workspace 'results\sirt-iteration-curves.json') -Raw |
        ConvertFrom-Json
    foreach ($summary in $sirt.summaries) {
        if ([string]$summary.leakage_check -ne 'pass') {
            $failures.Add("SIRT leakage check is not pass for $($summary.dataset).")
        }
        $checkpoints = @($summary.candidate_iterations | ForEach-Object { [int]$_ })
        if (($checkpoints -join ',') -ne '0,5,10,20,40,80,160') {
            $failures.Add("SIRT checkpoints are incomplete for $($summary.dataset).")
        }
        if ([int]$summary.selected_iterations -ne 80) {
            $failures.Add("SIRT depth was not frozen at the template semi-convergence minimum for $($summary.dataset).")
        }
    }
}
catch { $failures.Add("SIRT curve check failed: $($_.Exception.Message)") }

try {
    $stability = Get-Content -LiteralPath (In-Workspace 'results\stability.json') -Raw |
        ConvertFrom-Json
    if ([string]$stability.status -ne 'pass') { $failures.Add('Stability pipeline status is not pass.') }
    foreach ($field in @('mean_variance_status', 'support_independence_status', 'common_noise_status')) {
        if ([string]$stability.noise_diagnostics.$field -ne 'pass') {
            $failures.Add("Stability noise diagnostic is not pass: $field")
        }
    }
    if ($stability.seeds.Count -ne 3) { $failures.Add('Stability analysis does not contain three seed batches.') }
    if ([int]$stability.replicates_per_seed -lt 40) {
        $failures.Add('Final stability analysis has fewer than 40 replicates per seed.')
    }
    if ([int]$stability.original.successful_replicates -ne 3 * [int]$stability.replicates_per_seed -or
        [int]$stability.proposed.successful_replicates -ne 3 * [int]$stability.replicates_per_seed) {
        $failures.Add('Stability replicate failures are present.')
    }
}
catch { $failures.Add("Stability check failed: $($_.Exception.Message)") }

$samples = Import-Csv -LiteralPath (In-Workspace 'results\<SOURCE_FILE_REDACTED>')
if ($samples.Count -ne 10) { $failures.Add("Sample row count is $($samples.Count), expected 10") }

$excel = $null
$matrixChecks = @()
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    foreach ($name in @('problem2', 'problem3')) {
        $csvPath = In-Workspace "results\$<SOURCE_FILE_REDACTED>"
        $xlsPath = In-Workspace "$<SOURCE_FILE_REDACTED>"
        $lines = [System.IO.File]::ReadAllLines($csvPath)
        $workbook = $null; $sheet = $null; $range = $null
        $mismatchCount = 0; $maximumDifference = 0.0; $rows = 0; $columns = 0
        try {
            if ($lines.Count -ne 256) { throw "$<SOURCE_FILE_REDACTED> has $($lines.Count) rows, expected 256" }
            $workbook = $excel.Workbooks.Open($xlsPath, 0, $true)
            $sheet = $workbook.Worksheets.Item(1)
            $range = $sheet.UsedRange
            $rows = [int]$range.Rows.Count; $columns = [int]$range.Columns.Count
            if ($rows -ne 256 -or $columns -ne 256) { throw "$<SOURCE_FILE_REDACTED> is ${rows}x${columns}" }
            $values = $range.Value2
            for ($row = 1; $row -le 256; $row++) {
                $fields = $lines[$row - 1].Split(',')
                if ($fields.Count -ne 256) { throw "$<SOURCE_FILE_REDACTED> row $row has $($fields.Count) columns" }
                for ($column = 1; $column -le 256; $column++) {
                    $expected = [double]::Parse($fields[$column - 1], [Globalization.CultureInfo]::InvariantCulture)
                    $difference = [Math]::Abs([double]$values[$row, $column] - $expected)
                    $maximumDifference = [Math]::Max($maximumDifference, $difference)
                    if ($difference -gt 1e-7) { $mismatchCount++ }
                }
            }
            if ($mismatchCount -gt 0) { $failures.Add("$name XLS/CSV mismatches: $mismatchCount") }
            $matrixChecks += [PSCustomObject]@{
                name = $name
                status = if ($mismatchCount -eq 0) { 'pass' } else { 'fail' }
                rows = $rows; columns = $columns
                mismatch_count = $mismatchCount
                maximum_absolute_difference = $maximumDifference
                sha256 = (Get-FileHash -LiteralPath $xlsPath -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
        finally {
            if ($null -ne $range) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($range) }
            if ($null -ne $sheet) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($sheet) }
            if ($null -ne $workbook) {
                $workbook.Close($false)
                [void][Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
            }
        }
    }
}
catch { $failures.Add("Excel matrix verification failed: $($_.Exception.Message)") }
finally {
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}

$xelatex = Get-Command xelatex -ErrorAction SilentlyContinue
$paperCompileStatus = 'needs_review'
$paperCompileReason = if ($GeneratedOnly) {
    'Generated-only reproduction does not include the static paper source.'
} elseif ($null -eq $xelatex) {
    'XeLaTeX is not installed in this environment.'
} else {
    'XeLaTeX is available, but page-by-page visual inspection remains a human check.'
}

$overall = if ($failures.Count -eq 0) { 'pass' } else { 'fail' }
$report = [PSCustomObject]@{
    status = $overall
    artifact_presence = if ($missing.Count -eq 0) { 'pass' } else { 'fail' }
    generated_json = $jsonStatus
    direction_semantics = if (@($failures | Where-Object { $_ -match 'Direction' }).Count -eq 0) { 'pass' } else { 'fail' }
    fit_evaluate_separation = if (@($failures | Where-Object { $_ -match 'Angle-step' }).Count -eq 0) { 'pass' } else { 'fail' }
    sirt_stopping_artifacts = if (@($failures | Where-Object { $_ -match 'SIRT' }).Count -eq 0) { 'pass' } else { 'fail' }
    stability_pipeline = if (@($failures | Where-Object { $_ -match 'Stability' }).Count -eq 0) { 'pass' } else { 'fail' }
    matrix_consistency = $matrixChecks
    paper_compile = $paperCompileStatus
    paper_compile_reason = $paperCompileReason
    mathematical_correctness = 'needs_review'
    external_validity = 'needs_review'
    failures = $failures
}
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath `
    (In-Workspace 'results\verification.json') -Encoding utf8NoBOM
$report | ConvertTo-Json -Depth 10
if ($overall -eq 'fail') { exit 1 }
