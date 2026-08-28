param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$invariant = [System.Globalization.CultureInfo]::InvariantCulture
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$outputDirectory = Join-Path $Workspace 'results\processed'
[void](New-Item -ItemType Directory -Force -Path $outputDirectory)
$scratchDirectory = Join-Path $Workspace 'scratch\xls-extraction'
[void](New-Item -ItemType Directory -Force -Path $scratchDirectory)

# Excel 12 can rewrite Compound File Binary metadata even when a workbook is
# opened read-only.  Work only on disposable copies so the allowed input files
# are never handed to the COM server after this copy operation.
$attachment1Source = Join-Path $Workspace 'input\data\<SOURCE_FILE_REDACTED>'
$attachment2Source = Join-Path $Workspace 'input\data\<SOURCE_FILE_REDACTED>'
$attachment1Working = Join-Path $scratchDirectory '<SOURCE_FILE_REDACTED>'
$attachment2Working = Join-Path $scratchDirectory '<SOURCE_FILE_REDACTED>'
Copy-Item -LiteralPath $attachment1Source -Destination $attachment1Working -Force
Copy-Item -LiteralPath $attachment2Source -Destination $attachment2Working -Force

function Convert-ToCsvField {
    param([object]$Value)

    if ($null -eq $Value) {
        return ''
    }
    if ($Value -is [string]) {
        return '"' + $Value.Replace('"', '""') + '"'
    }
    if ($Value -is [double] -or $Value -is [single]) {
        return $Value.ToString('G17', $invariant)
    }
    if ($Value -is [System.IFormattable]) {
        return $Value.ToString($null, $invariant)
    }
    return '"' + $Value.ToString().Replace('"', '""') + '"'
}

function Export-WorksheetCsv {
    param(
        [object]$Worksheet,
        [string]$Destination
    )

    $usedRange = $null
    $writer = $null
    try {
        $usedRange = $Worksheet.UsedRange
        $rowCount = $usedRange.Rows.Count
        $columnCount = $usedRange.Columns.Count
        $values = $usedRange.Value2
        $writer = [System.IO.StreamWriter]::new($Destination, $false, $utf8NoBom)

        for ($row = 1; $row -le $rowCount; $row++) {
            $fields = [System.Collections.Generic.List[string]]::new()
            for ($column = 1; $column -le $columnCount; $column++) {
                [void]$fields.Add((Convert-ToCsvField $values[$row, $column]))
            }
            $writer.WriteLine([string]::Join(',', $fields))
        }
    }
    finally {
        if ($writer) {
            $writer.Dispose()
        }
        if ($usedRange) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($usedRange)
        }
    }
}

$excel = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    try { $excel.AutomationSecurity = 3 } catch { }

    $workbooks = @(
        @{
            Path = $attachment1Working
            Sheets = @(
                @{ Index = 1; File = '<SOURCE_FILE_REDACTED>' }
                @{ Index = 2; File = '<SOURCE_FILE_REDACTED>' }
                @{ Index = 3; File = '<SOURCE_FILE_REDACTED>' }
                @{ Index = 4; File = '<SOURCE_FILE_REDACTED>' }
            )
        },
        @{
            Path = $attachment2Working
            Sheets = @(
                @{ Index = 1; File = '<SOURCE_FILE_REDACTED>' }
            )
        }
    )

    foreach ($specification in $workbooks) {
        $workbook = $null
        try {
            $workbook = $excel.Workbooks.Open($specification.Path, 0, $true)
            foreach ($sheetSpecification in $specification.Sheets) {
                $worksheet = $null
                try {
                    $worksheet = $workbook.Worksheets.Item([int]$sheetSpecification.Index)
                    $destination = Join-Path $outputDirectory $sheetSpecification.File
                    Export-WorksheetCsv -Worksheet $worksheet -Destination $destination
                }
                finally {
                    if ($worksheet) {
                        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($worksheet)
                    }
                }
            }
        }
        finally {
            if ($workbook) {
                $workbook.Close($false)
                [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($workbook)
            }
        }
    }
}
finally {
    if ($excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
    }
}

Write-Output '[pass] Excel workbooks exported to results/processed.'
