param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$inputDir = [System.IO.Path]::Combine($workspacePath, 'input', 'data')
$outputDir = [System.IO.Path]::Combine($workspacePath, 'results', 'extracted')

if (-not (Test-Path -LiteralPath $inputDir -PathType Container)) {
    throw "Input directory does not exist: $inputDir"
}
[System.IO.Directory]::CreateDirectory($outputDir) | Out-Null

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$invariant = [System.Globalization.CultureInfo]::InvariantCulture
$manifest = [System.Collections.Generic.List[object]]::new()

function Convert-CellToText {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) {
        return ''
    }
    if ($Value -is [double] -or $Value -is [single] -or
        $Value -is [decimal] -or $Value -is [int] -or
        $Value -is [long] -or $Value -is [short]) {
        return [System.Convert]::ToString($Value, $invariant)
    }
    $text = [System.Convert]::ToString($Value, $invariant)
    return ($text -replace "`t", ' ' -replace "`r?`n", ' ')
}

$excel = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false

    $workbooks = Get-ChildItem -LiteralPath $inputDir -File -Filter '*.xls' | Sort-Object Name
    foreach ($file in $workbooks) {
        $workbook = $null
        try {
            $workbook = $excel.Workbooks.Open($file.FullName, 0, $true)
            $workbookHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            foreach ($worksheet in @($workbook.Worksheets)) {
                $range = $null
                try {
                    $range = $worksheet.UsedRange
                    $rowCount = [int]$range.Rows.Count
                    $columnCount = [int]$range.Columns.Count
                    $safeSheet = ($worksheet.Name -replace '[^\p{L}\p{Nd}_-]', '_')
                    $outputName = '{0}__{1}.tsv' -f $workbookHash.Substring(0, 12), $safeSheet
                    $outputPath = [System.IO.Path]::Combine($outputDir, $outputName)
                    $writer = [System.IO.StreamWriter]::new($outputPath, $false, $utf8NoBom)
                    try {
                        $values = $range.Value2
                        if ($rowCount -eq 1 -and $columnCount -eq 1) {
                            $writer.WriteLine((Convert-CellToText $values))
                        }
                        else {
                            $rowLower = $values.GetLowerBound(0)
                            $columnLower = $values.GetLowerBound(1)
                            for ($rowOffset = 0; $rowOffset -lt $rowCount; $rowOffset++) {
                                $cells = [System.Collections.Generic.List[string]]::new($columnCount)
                                for ($columnOffset = 0; $columnOffset -lt $columnCount; $columnOffset++) {
                                    $value = $values[($rowLower + $rowOffset), ($columnLower + $columnOffset)]
                                    $cells.Add((Convert-CellToText $value))
                                }
                                $writer.WriteLine([string]::Join("`t", $cells))
                            }
                        }
                    }
                    finally {
                        $writer.Dispose()
                    }
                    $outputHash = (Get-FileHash -LiteralPath $outputPath -Algorithm SHA256).Hash.ToLowerInvariant()
                    $manifest.Add([ordered]@{
                        workbook = $file.Name
                        workbook_sha256 = $workbookHash
                        sheet = [string]$worksheet.Name
                        rows = $rowCount
                        columns = $columnCount
                        extracted_file = $outputName
                        extracted_sha256 = $outputHash
                    })
                }
                finally {
                    if ($null -ne $range) {
                        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($range) | Out-Null
                    }
                    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($worksheet) | Out-Null
                }
            }
        }
        finally {
            if ($null -ne $workbook) {
                $workbook.Close($false)
                [System.Runtime.InteropServices.Marshal]::ReleaseComObject($workbook) | Out-Null
            }
        }
    }
}
finally {
    if ($null -ne $excel) {
        try { $excel.Quit() } catch { }
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$manifestPath = [System.IO.Path]::Combine($outputDir, 'manifest.json')
[System.IO.File]::WriteAllText(
    $manifestPath,
    ($manifest | ConvertTo-Json -Depth 5),
    $utf8NoBom
)

Write-Output ('[PASS] extracted {0} sheets to {1}' -f $manifest.Count, $outputDir)
