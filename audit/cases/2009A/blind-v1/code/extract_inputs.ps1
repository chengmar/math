param(
    [Parameter(Mandatory = $false)]
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$problemPath = Join-Path $Workspace 'input\problem\<SOURCE_FILE_REDACTED>'
$dataPath = Join-Path $Workspace 'input\data\<SOURCE_FILE_REDACTED>'
$resultsDir = Join-Path $Workspace 'results'
$problemOutput = Join-Path $resultsDir 'problem-extracted.txt'
$csvOutput = Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'
$metadataOutput = Join-Path $resultsDir 'input-metadata.json'

if (-not (Test-Path -LiteralPath $problemPath -PathType Leaf)) {
    throw "Missing problem file: $problemPath"
}
if (-not (Test-Path -LiteralPath $dataPath -PathType Leaf)) {
    throw "Missing data file: $dataPath"
}
[System.IO.Directory]::CreateDirectory($resultsDir) | Out-Null

# Extract the legacy Word document without modifying it.
$word = $null
$document = $null
$content = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $<SOURCE_FILE_REDACTED>uments.Open($problemPath, $false, $true)
    $content = $document.Content
    [System.IO.File]::WriteAllText($problemOutput, [string]$content.Text, $utf8NoBom)
}
finally {
    if ($null -ne $content) {
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($content) | Out-Null
    }
    if ($null -ne $document) {
        $document.Close([ref]0)
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) | Out-Null
    }
    if ($null -ne $word) {
        try { $word.Quit() } catch { }
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) | Out-Null
    }
}

# Extract the numeric portion of the legacy Excel workbook.
$excel = $null
$book = $null
$sheet = $null
$range = $null
$usedRange = $null
$endCell = $null
$bottomCell = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $book = $excel.Workbooks.Open($dataPath, 0, $true)
    $sheet = $book.Worksheets.Item(1)
    $worksheetCount = [int]$book.Worksheets.Count
    $usedRange = $sheet.UsedRange
    $usedRangeRows = [int]$usedRange.Rows.Count
    $usedRangeColumns = [int]$usedRange.Columns.Count

    $bottomCell = $sheet.Cells.Item($sheet.Rows.Count, 4)
    $endCell = $bottomCell.End(-4162) # xlUp
    $lastRow = [int]$endCell.Row
    if ($lastRow -lt 3) {
        throw 'The first worksheet contains no experiment rows.'
    }
    $range = $sheet.Range("A1:H$lastRow")
    $values = $range.Value2

    $writer = New-Object System.IO.StreamWriter($csvOutput, $false, $utf8NoBom)
    try {
        $writer.WriteLine('torque_Nm,speed_rpm,time_s')
        for ($row = 3; $row -le $lastRow; $row++) {
            $torque = [double]$values[$row, 2]
            $rpm = [double]$values[$row, 3]
            $time = [double]$values[$row, 4]
            $writer.WriteLine(('{0},{1},{2}' -f
                $torque.ToString('R', $culture),
                $rpm.ToString('R', $culture),
                $time.ToString('R', $culture)))
        }
    }
    finally {
        $writer.Dispose()
    }

    $metadata = [ordered]@{
        source_sheet = [string]$sheet.Name
        worksheet_count = $worksheetCount
        used_range_rows = $usedRangeRows
        used_range_columns = $usedRangeColumns
        last_numeric_row = $lastRow
        sample_count = $lastRow - 2
        initial_speed_rpm_nominal = [double]$values[3, 5]
        final_speed_rpm_nominal = [double]$values[3, 6]
        equivalent_inertia_kg_m2 = [double]$values[3, 7]
        mechanical_inertia_kg_m2 = [double]$values[3, 8]
    }
    $json = $metadata | ConvertTo-Json
    [System.IO.File]::WriteAllText($metadataOutput, $json + [Environment]::NewLine, $utf8NoBom)
}
finally {
    if ($null -ne $usedRange) {
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($usedRange) | Out-Null
    }
    if ($null -ne $range) {
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($range) | Out-Null
    }
    if ($null -ne $endCell) {
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($endCell) | Out-Null
    }
    if ($null -ne $bottomCell) {
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($bottomCell) | Out-Null
    }
    if ($null -ne $sheet) {
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet) | Out-Null
    }
    if ($null -ne $book) {
        $book.Close($false)
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($book) | Out-Null
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel) | Out-Null
    }
}

[GC]::Collect()
[GC]::WaitForPendingFinalizers()
Write-Output '[pass] legacy inputs extracted read-only'
