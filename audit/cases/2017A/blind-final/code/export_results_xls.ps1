param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

function Assert-InWorkspace {
    param(
        [string]$Candidate,
        [string]$Root
    )
    $fullCandidate = [System.IO.Path]::GetFullPath($Candidate)
    $fullRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    if (-not $fullCandidate.StartsWith($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes workspace: $fullCandidate"
    }
    return $fullCandidate
}

$workspaceRoot = [System.IO.Path]::GetFullPath($Workspace)
$jobs = @(
    [PSCustomObject]@{
        name = 'problem2'
        source = Assert-InWorkspace -Candidate (Join-Path $workspaceRoot 'results\<SOURCE_FILE_REDACTED>') -Root $workspaceRoot
        destination = Assert-InWorkspace -Candidate (Join-Path $workspaceRoot '<SOURCE_FILE_REDACTED>') -Root $workspaceRoot
    },
    [PSCustomObject]@{
        name = 'problem3'
        source = Assert-InWorkspace -Candidate (Join-Path $workspaceRoot 'results\<SOURCE_FILE_REDACTED>') -Root $workspaceRoot
        destination = Assert-InWorkspace -Candidate (Join-Path $workspaceRoot '<SOURCE_FILE_REDACTED>') -Root $workspaceRoot
    }
)

$excel = $null
$results = @()
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    foreach ($job in $jobs) {
        if (-not (Test-Path -LiteralPath $job.source -PathType Leaf)) {
            throw "Missing generated CSV: $($job.source)"
        }
        if (Test-Path -LiteralPath $job.destination -PathType Leaf) {
            Remove-Item -LiteralPath $job.destination -Force
        }
        $workbook = $null
        $sheet = $null
        $range = $null
        try {
            $workbook = $excel.Workbooks.Open($job.source)
            $sheet = $workbook.Worksheets.Item(1)
            $sheet.Name = $job.name
            $range = $sheet.UsedRange
            if ([int]$range.Rows.Count -ne 256 -or [int]$range.Columns.Count -ne 256) {
                throw "Unexpected CSV dimensions for $($job.name): $($range.Rows.Count)x$($range.Columns.Count)"
            }
            $range.NumberFormat = '0.0000'
            # 56 = xlExcel8,<SOURCE_FILE_REDACTED> format requested by the problem.
            $workbook.SaveAs($job.destination, 56)
            $results += [PSCustomObject]@{
                name = $job.name
                status = 'pass'
                rows = [int]$range.Rows.Count
                columns = [int]$range.Columns.Count
                destination = $job.destination
            }
        }
        finally {
            if ($null -ne $range) {
                [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($range)
            }
            if ($null -ne $sheet) {
                [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($sheet)
            }
            if ($null -ne $workbook) {
                $workbook.Close($false)
                [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
            }
        }
    }
    [PSCustomObject]@{
        status = 'pass'
        excel_version = $excel.Version
        outputs = $results
    } | ConvertTo-Json -Depth 5
}
catch {
    [PSCustomObject]@{
        status = 'fail'
        error = $_.Exception.Message
    } | ConvertTo-Json -Depth 3
    exit 1
}
finally {
    if ($null -ne $excel) {
        $excel.Quit()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
