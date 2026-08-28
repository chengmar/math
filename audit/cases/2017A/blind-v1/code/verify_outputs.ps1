param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = [System.IO.Path]::GetFullPath($Workspace)
$failures = [System.Collections.Generic.List[string]]::new()

function In-Workspace {
    param([string]$RelativePath)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot $RelativePath))
    $prefix = $workspaceRoot.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes workspace: $candidate"
    }
    return $candidate
}

$required = @(
    'problem-analysis.md',
    'data-audit.md',
    'assumptions.yaml',
    'variables.yaml',
    'model-selection.md',
    'solution-report.yaml',
    'reproducibility.yaml',
    'paper\main.tex',
    'paper\paper.md',
    'paper\generated-values.tex',
    '<SOURCE_FILE_REDACTED>',
    '<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\calibration.json',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\model-comparison.json',
    'results\stability.json',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'reports\training-memory-usage.md'
)
$missing = @()
foreach ($relative in $required) {
    $path = In-Workspace $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $missing += $relative
    }
}
if ($missing.Count -gt 0) {
    $failures.Add("Missing required files: $($missing -join ', ')")
}

$jsonFiles = @(
    'results\calibration.json',
    'results\model-comparison.json',
    'results\sample-values.json',
    'results\problem2-geometry.json',
    'results\problem3-geometry.json',
    'results\stability.json',
    'results\numerical-summary.json'
)
$jsonStatus = @()
foreach ($relative in $jsonFiles) {
    try {
        $document = Get-Content -LiteralPath (In-Workspace $relative) -Raw | ConvertFrom-Json
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

$directions = Get-Content -LiteralPath (In-Workspace 'results\<SOURCE_FILE_REDACTED>')
if ($directions.Count -ne 181) {
    $failures.Add("<SOURCE_FILE_REDACTED> line count is $($directions.Count), expected 181")
}
$samples = Get-Content -LiteralPath (In-Workspace 'results\<SOURCE_FILE_REDACTED>')
if ($samples.Count -ne 11) {
    $failures.Add("<SOURCE_FILE_REDACTED> line count is $($samples.Count), expected 11")
}
$memoryUsage = Get-Content -LiteralPath (In-Workspace 'reports\training-memory-usage.md') -Raw
if ($memoryUsage -match 'decision:\s*pending') {
    $failures.Add('training-memory-usage.md still contains a pending decision')
}

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
        if ($lines.Count -ne 256) {
            $failures.Add("$<SOURCE_FILE_REDACTED> has $($lines.Count) rows, expected 256")
            continue
        }
        $workbook = $null
        $sheet = $null
        $range = $null
        try {
            $workbook = $excel.Workbooks.Open($xlsPath, 0, $true)
            $sheet = $workbook.Worksheets.Item(1)
            $range = $sheet.UsedRange
            $rows = [int]$range.Rows.Count
            $columns = [int]$range.Columns.Count
            if ($rows -ne 256 -or $columns -ne 256) {
                $failures.Add("$<SOURCE_FILE_REDACTED> is ${rows}x${columns}, expected 256x256")
            }
            $values = $range.Value2
            $maximumDifference = 0.0
            $mismatchCount = 0
            for ($row = 1; $row -le 256; $row++) {
                $fields = $lines[$row - 1].Split(',')
                if ($fields.Count -ne 256) {
                    $failures.Add("$<SOURCE_FILE_REDACTED> row $row has $($fields.Count) columns")
                    break
                }
                for ($column = 1; $column -le 256; $column++) {
                    $expected = [double]::Parse(
                        $fields[$column - 1],
                        [System.Globalization.CultureInfo]::InvariantCulture)
                    $actual = [double]$values[$row, $column]
                    $difference = [Math]::Abs($actual - $expected)
                    $maximumDifference = [Math]::Max($maximumDifference, $difference)
                    if ($difference -gt 0.0000001) {
                        $mismatchCount++
                    }
                }
            }
            if ($mismatchCount -gt 0) {
                $failures.Add("$name XLS/CSV mismatches: $mismatchCount")
            }
            $matrixChecks += [PSCustomObject]@{
                name = $name
                status = if ($mismatchCount -eq 0 -and $rows -eq 256 -and $columns -eq 256) { 'pass' } else { 'fail' }
                rows = $rows
                columns = $columns
                mismatch_count = $mismatchCount
                maximum_absolute_difference = $maximumDifference
                sha256 = (Get-FileHash -LiteralPath $xlsPath -Algorithm SHA256).Hash
            }
        }
        finally {
            if ($null -ne $range) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($range) }
            if ($null -ne $sheet) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($sheet) }
            if ($null -ne $workbook) {
                $workbook.Close($false)
                [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
            }
        }
    }
}
catch {
    $failures.Add("Excel verification failed: $($_.Exception.Message)")
}
finally {
    if ($null -ne $excel) {
        $excel.Quit()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$xelatex = Get-Command xelatex -ErrorAction SilentlyContinue
$paperCompileStatus = if ($null -eq $xelatex) { 'needs_review' } else { 'needs_review' }
$overall = if ($failures.Count -eq 0) { 'pass' } else { 'fail' }
$report = [PSCustomObject]@{
    status = $overall
    artifact_presence = if ($missing.Count -eq 0) { 'pass' } else { 'fail' }
    generated_json = $jsonStatus
    matrix_consistency = $matrixChecks
    directions_row_count = $directions.Count - 1
    sample_row_count = $samples.Count - 1
    training_memory_decisions = if ($memoryUsage -notmatch 'decision:\s*pending') { 'pass' } else { 'fail' }
    paper_compile = $paperCompileStatus
    paper_compile_reason = if ($null -eq $xelatex) { 'XeLaTeX is not installed in this environment.' } else { 'Compilation must be run explicitly and visually inspected.' }
    mathematical_correctness = 'needs_review'
    external_validity = 'needs_review'
    failures = $failures
}
$verificationPath = In-Workspace 'results\verification.json'
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $verificationPath -Encoding utf8NoBOM
$report | ConvertTo-Json -Depth 8
if ($overall -eq 'fail') {
    exit 1
}
