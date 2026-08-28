param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$inputDir = [System.IO.Path]::Combine($workspacePath, 'input', 'data')
$outputDir = [System.IO.Path]::Combine($workspacePath, 'results', 'extracted')
$workingDir = [System.IO.Path]::Combine($workspacePath, 'results', 'extraction-working-copies')
if (-not (Test-Path -LiteralPath $inputDir -PathType Container)) {
    throw "[fail] input directory does not exist: $inputDir"
}
foreach ($directory in @($outputDir, $workingDir)) {
    if (Test-Path -LiteralPath $directory) {
        Remove-Item -LiteralPath $directory -Recurse -Force
    }
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$invariant = [System.Globalization.CultureInfo]::InvariantCulture
$manifest = [System.Collections.Generic.List[object]]::new()
$sources = [System.Collections.Generic.List[object]]::new()

function Convert-CellToText {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return '' }
    if ($Value -is [double] -or $Value -is [single] -or
        $Value -is [decimal] -or $Value -is [int] -or
        $Value -is [long] -or $Value -is [System.Int16]) {
        return [System.Convert]::ToString($Value, $invariant)
    }
    $text = [System.Convert]::ToString($Value, $invariant)
    return ($text -replace "`t", ' ' -replace "`r?`n", ' ')
}

foreach ($file in (Get-ChildItem -LiteralPath $inputDir -File -Filter '*.xls' | Sort-Object Name)) {
    $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $workingPath = [System.IO.Path]::Combine($workingDir, $file.Name)
    [System.IO.File]::Copy($file.FullName, $workingPath, $false)
    $copyHash = (Get-FileHash -LiteralPath $workingPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($copyHash -ne $sourceHash) {
        throw "[fail] isolated working copy hash mismatch: $($file.Name)"
    }
    $sources.Add([ordered]@{
        Name = $file.Name
        OriginalPath = $file.FullName
        WorkingPath = $workingPath
        SourceHash = $sourceHash
    })
}

$excel = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    foreach ($source in $sources) {
        $workbook = $null
        try {
            # Excel is allowed to touch only this disposable copy.  The source
            # attachment is never passed to Office COM.
            $workbook = $excel.Workbooks.Open($source.WorkingPath, 0, $true)
            foreach ($worksheet in @($workbook.Worksheets)) {
                $range = $null
                try {
                    $range = $worksheet.UsedRange
                    $rowCount = [int]$range.Rows.Count
                    $columnCount = [int]$range.Columns.Count
                    $safeSheet = ($worksheet.Name -replace '[^\p{L}\p{Nd}_-]', '_')
                    $outputName = '{0}__{1}.tsv' -f $source.SourceHash.Substring(0, 12), $safeSheet
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
                        workbook = $source.Name
                        workbook_sha256 = $source.SourceHash
                        source_isolation = 'Excel opened a disposable results/ copy, never input/data'
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

foreach ($source in $sources) {
    $postHash = (Get-FileHash -LiteralPath $source.OriginalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($postHash -ne $source.SourceHash) {
        throw "[fail] source attachment changed even though Excel received only a disposable copy: $($source.Name)"
    }
}

$manifestPath = [System.IO.Path]::Combine($outputDir, 'manifest.json')
[System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 5), $utf8NoBom)
Remove-Item -LiteralPath $workingDir -Recurse -Force
Write-Output ('[pass] extracted {0} sheets from isolated copies; source input hashes unchanged' -f $manifest.Count)
