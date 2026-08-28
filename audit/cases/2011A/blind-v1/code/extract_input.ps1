param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$inputPath = Join-Path $Workspace 'input\data\<SOURCE_FILE_REDACTED>'
$outputDir = Join-Path $Workspace 'results\raw'

if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
    throw "Input workbook not found: $inputPath"
}
if (-not (Test-Path -LiteralPath $outputDir -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $outputDir)
}

function ConvertTo-CsvField {
    param([object]$Value)

    if ($null -eq $Value) {
        return '""'
    }
    if ($Value -is [double] -or $Value -is [float] -or
        $Value -is [decimal] -or $Value -is [int] -or
        $Value -is [long]) {
        $text = [System.Convert]::ToString(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    } else {
        $text = [string]$Value
    }
    return '"' + $text.Replace('"', '""') + '"'
}

$excel = $null
$workbook = $null
$manifestSheets = @()

try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excelVersion = [string]$excel.Version

    $workbook = $excel.Workbooks.Open($inputPath, 0, $true)
    $sheetIndex = 0
    foreach ($worksheet in $workbook.Worksheets) {
        $sheetIndex += 1
        $usedRange = $worksheet.UsedRange
        $rowCount = [int]$usedRange.Rows.Count
        $columnCount = [int]$usedRange.Columns.Count
        $values = $usedRange.Value2
        $csvName = "sheet_$<SOURCE_FILE_REDACTED>"
        $csvPath = Join-Path $outputDir $csvName
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        $writer = New-Object System.IO.StreamWriter($csvPath, $false, $utf8NoBom)

        try {
            if ($rowCount -eq 1 -and $columnCount -eq 1) {
                $writer.WriteLine((ConvertTo-CsvField $values))
            } else {
                $rowLower = $values.GetLowerBound(0)
                $columnLower = $values.GetLowerBound(1)
                for ($row = 0; $row -lt $rowCount; $row += 1) {
                    $fields = New-Object System.Collections.Generic.List[string]
                    for ($column = 0; $column -lt $columnCount; $column += 1) {
                        $value = $values[($rowLower + $row), ($columnLower + $column)]
                        $fields.Add((ConvertTo-CsvField $value))
                    }
                    $writer.WriteLine(($fields -join ','))
                }
            }
        } finally {
            $writer.Dispose()
        }

        $manifestSheets += [PSCustomObject]@{
            index = $sheetIndex
            name = [string]$worksheet.Name
            output = "results/raw/$csvName"
            rows = $rowCount
            columns = $columnCount
            start_row = [int]$usedRange.Row
            start_column = [int]$usedRange.Column
        }

        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($usedRange)
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($worksheet)
    }

    $manifest = [ordered]@{
        status = 'pass'
        source = 'input/data/<SOURCE_FILE_REDACTED>'
        source_sha256 = (Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash.ToLowerInvariant()
        extractor = 'Excel COM read-only Value2 export'
        excel_version = $excelVersion
        sheets = $manifestSheets
    }
    $manifestPath = Join-Path $outputDir 'extraction_manifest.json'
    $manifestJson = $manifest | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText(
        $manifestPath,
        $manifestJson + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
} finally {
    if ($null -ne $workbook) {
        $workbook.Close($false)
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

Write-Output 'pass: workbook extracted to results/raw'
