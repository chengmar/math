param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$inputDir = Join-Path $Workspace 'input\data'
$outputDir = Join-Path $Workspace 'results\extracted'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

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
    }
    else {
        $text = [string]$Value
    }
    return '"' + $text.Replace('"', '""') + '"'
}

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$manifest = @()

try {
    $workbooks = @(Get-ChildItem -LiteralPath $inputDir -Filter '*.xls' |
        Sort-Object Name)
    for ($wi = 0; $wi -lt $workbooks.Count; $wi++) {
        $book = $workbooks[$wi]
        $wb = $excel.Workbooks.Open($book.FullName, 0, $true)
        try {
            for ($si = 1; $si -le $wb.Worksheets.Count; $si++) {
                $ws = $wb.Worksheets.Item($si)
                $range = $ws.UsedRange
                $rows = [int]$range.Rows.Count
                $cols = [int]$range.Columns.Count
                $fileName = 'workbook{0:D2}_sheet{1:D2}.csv' -f ($wi + 1), $si
                $csvPath = Join-Path $outputDir $fileName
                $encoding = New-Object System.Text.UTF8Encoding($false)
                $writer = New-Object System.IO.StreamWriter($csvPath, $false, $encoding)
                try {
                    for ($r = 1; $r -le $rows; $r++) {
                        $fields = New-Object System.Collections.Generic.List[string]
                        for ($c = 1; $c -le $cols; $c++) {
                            $fields.Add((ConvertTo-CsvField $range.Cells.Item($r, $c).Value2))
                        }
                        $writer.WriteLine([string]::Join(',', $fields))
                    }
                }
                finally {
                    $writer.Dispose()
                }
                $manifest += [ordered]@{
                    workbook_index = $wi + 1
                    workbook_file = $book.Name
                    sheet_index = $si
                    sheet_name = [string]$ws.Name
                    rows = $rows
                    columns = $cols
                    csv = $fileName
                }
            }
        }
        finally {
            $wb.Close($false)
        }
    }
}
finally {
    $excel.Quit()
}

$manifestPath = Join-Path $outputDir 'manifest.json'
$manifest | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath $manifestPath -Encoding utf8NoBOM
Write-Output "[PASS] extracted $($manifest.Count) worksheets to $outputDir"
