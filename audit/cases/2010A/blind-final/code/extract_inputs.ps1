param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$inputDirectory = Join-Path $workspacePath 'input\data'
$outputDirectory = Join-Path $workspacePath 'results\extracted'
$workingDirectory = Join-Path $workspacePath 'results\_xls_work'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$invariant = [System.Globalization.CultureInfo]::InvariantCulture

[void](New-Item -ItemType Directory -Force -Path $outputDirectory)
[void](New-Item -ItemType Directory -Force -Path $workingDirectory)

function Convert-ToCsvField {
    param([object]$Value)

    if ($null -eq $Value) {
        return '""'
    }
    if ($Value -is [double] -or $Value -is [single] -or
        $Value -is [decimal] -or $Value -is [int] -or
        $Value -is [long]) {
        $text = [System.Convert]::ToString($Value, $invariant)
    }
    else {
        $text = [string]$Value
    }
    return '"' + $text.Replace('"', '""') + '"'
}

function Export-WorksheetCsv {
    param(
        [object]$Worksheet,
        [string]$Destination
    )

    $range = $null
    $writer = $null
    try {
        $range = $Worksheet.UsedRange
        $rows = [int]$range.Rows.Count
        $columns = [int]$range.Columns.Count
        $values = $range.Value2
        $writer = [System.IO.StreamWriter]::new($Destination, $false, $utf8NoBom)
        for ($row = 1; $row -le $rows; $row++) {
            $fields = [System.Collections.Generic.List[string]]::new()
            for ($column = 1; $column -le $columns; $column++) {
                [void]$fields.Add((Convert-ToCsvField $values[$row, $column]))
            }
            $writer.WriteLine([string]::Join(',', $fields))
        }
        return [ordered]@{ rows = $rows; columns = $columns }
    }
    finally {
        if ($writer) { $writer.Dispose() }
        if ($range) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($range)
        }
    }
}

$excel = $null
$manifest = @()
try {
    $sources = @(Get-ChildItem -LiteralPath $inputDirectory -Filter '*.xls' | Sort-Object Name)
    if ($sources.Count -ne 2) {
        throw "Expected exactly two XLS inputs, found $($sources.Count)"
    }

    $workingBooks = @()
    foreach ($source in $sources) {
        $workingPath = Join-Path $workingDirectory $source.Name
        Copy-Item -LiteralPath $source.FullName -Destination $workingPath -Force
        $workingBooks += [ordered]@{
            source = $source
            working_path = $workingPath
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $source.FullName).Hash.ToLowerInvariant()
        }
    }

    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    try { $excel.AutomationSecurity = 3 } catch { }

    for ($workbookIndex = 0; $workbookIndex -lt $workingBooks.Count; $workbookIndex++) {
        $specification = $workingBooks[$workbookIndex]
        $workbook = $null
        try {
            $workbook = $excel.Workbooks.Open($specification.working_path, 0, $true)
            for ($sheetIndex = 1; $sheetIndex -le $workbook.Worksheets.Count; $sheetIndex++) {
                $worksheet = $null
                try {
                    $worksheet = $workbook.Worksheets.Item($sheetIndex)
                    $fileName = 'workbook{0:D2}_sheet{1:D2}.csv' -f ($workbookIndex + 1), $sheetIndex
                    $destination = Join-Path $outputDirectory $fileName
                    $dimensions = Export-WorksheetCsv -Worksheet $worksheet -Destination $destination
                    $manifest += [ordered]@{
                        workbook_index = $workbookIndex + 1
                        workbook_file = $specification.source.Name
                        workbook_sha256 = $specification.sha256
                        sheet_index = $sheetIndex
                        sheet_name = [string]$worksheet.Name
                        rows = $dimensions.rows
                        columns = $dimensions.columns
                        csv = $fileName
                    }
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
    if (Test-Path -LiteralPath $workingDirectory) {
        $resolvedWork = [System.IO.Path]::GetFullPath($workingDirectory)
        $resolvedResults = [System.IO.Path]::GetFullPath((Join-Path $workspacePath 'results'))
        if (-not $resolvedWork.StartsWith($resolvedResults + [System.IO.Path]::DirectorySeparatorChar)) {
            throw "Refusing to remove unexpected XLS working directory: $resolvedWork"
        }
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force
    }
}

$manifestJson = $manifest | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText(
    (Join-Path $outputDirectory 'manifest.json'),
    $manifestJson + [Environment]::NewLine,
    $utf8NoBom
)
Write-Output "[PASS] extracted $($manifest.Count) worksheets from disposable XLS copies"
