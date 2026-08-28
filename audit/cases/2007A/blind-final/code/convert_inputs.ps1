param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$Workspace = [System.IO.Path]::GetFullPath($Workspace)

function Assert-WithinWorkspace {
    param([string]$Path)
    $root = [System.IO.Path]::GetFullPath($Workspace).TrimEnd('\')
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes workspace: $full"
    }
    return $full
}

$archive = Join-Path $Workspace 'input\attachments\<SOURCE_FILE_REDACTED>'
$problemDoc = Join-Path $Workspace 'input\problem\<SOURCE_FILE_REDACTED>'
$extractDir = Assert-WithinWorkspace (Join-Path $Workspace '_work\extracted-be8aa57c')
$convertDir = Assert-WithinWorkspace (Join-Path $Workspace '_work\converted')
$unrar = '<ABSOLUTE_PATH>'

foreach ($required in @($archive, $problemDoc, $unrar)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required input/tool missing: $required"
    }
}

if (-not (Test-Path -LiteralPath $extractDir -PathType Container)) {
    New-Item -ItemType Directory -Path $extractDir | Out-Null
    & $unrar x -p- -o- $archive $extractDir
    if ($LASTEXITCODE -ne 0) {
        throw "UnRAR failed with exit code $LASTEXITCODE"
    }
}
if (-not (Test-Path -LiteralPath $convertDir -PathType Container)) {
    New-Item -ItemType Directory -Path $convertDir | Out-Null
}

$documents = @(
    @{ Name = 'problem'; Path = $problemDoc },
    @{ Name = 'A2007App1'; Path = (Join-Path $extractDir '<SOURCE_FILE_REDACTED>') },
    @{ Name = 'A2007App2'; Path = (Join-Path $extractDir '<SOURCE_FILE_REDACTED>') }
)

foreach ($entry in $documents) {
    if (-not (Test-Path -LiteralPath $entry.Path -PathType Leaf)) {
        throw "Document missing after extraction: $($entry.Path)"
    }
    # Word 12 can invalidate its automation server after closing a converted
    # legacy document, so isolate every source document in its own instance.
    $word = $null
    $doc = $null
    try {
        $word = New-Object -ComObject Word.Application
        $word.Visible = $false
        $word.DisplayAlerts = 0
        # msoAutomationSecurityForceDisable
        $word.AutomationSecurity = 3
        $doc = $<SOURCE_FILE_REDACTED>uments.Open($entry.Path, $false, $true, $false)
        $textPath = Join-Path $convertDir ($entry.Name + '.txt')
        $htmlPath = Join-Path $convertDir ($entry.Name + '.html')
        $pdfPath = Join-Path $convertDir ($entry.Name + '.pdf')
        $metaPath = Join-Path $convertDir ($entry.Name + '-metadata.json')
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($textPath, $doc.Content.Text, $utf8NoBom)
        $metadata = [ordered]@{
            source = [System.IO.Path]::GetFileName($entry.Path)
            characters = $doc.Characters.Count
            paragraphs = $doc.Paragraphs.Count
            tables = $doc.Tables.Count
            inline_shapes = $doc.InlineShapes.Count
            floating_shapes = $doc.Shapes.Count
        } | ConvertTo-Json
        [System.IO.File]::WriteAllText($metaPath, $metadata, $utf8NoBom)
        # wdExportFormatPDF = 17
        $doc.ExportAsFixedFormat($pdfPath, 17)
        # wdFormatFilteredHTML = 10. Direct COM arguments are required by Word 12.
        $doc.SaveAs($htmlPath, 10)
    }
    finally {
        if ($null -ne $doc) {
            try { $doc.Close($false) } catch { }
            try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null } catch { }
        }
        if ($null -ne $word) {
            try { $word.Quit() } catch { }
            try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null } catch { }
        }
    }
}

$excel = $null
$workbook = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AutomationSecurity = 3
    $xlsPath = Join-Path $extractDir '<SOURCE_FILE_REDACTED>'
    $xlsxPath = Join-Path $convertDir '<SOURCE_FILE_REDACTED>'
    $workbook = $excel.Workbooks.Open($xlsPath, 0, $true)
    # xlOpenXMLWorkbook = 51
    $workbook.SaveAs($xlsxPath, 51)

    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $sheetMetadata = @()
    for ($sheetIndex = 1; $sheetIndex -le $workbook.Worksheets.Count; $sheetIndex++) {
        $sheet = $null
        $used = $null
        $writer = $null
        try {
            $sheet = $workbook.Worksheets.Item($sheetIndex)
            $used = $sheet.UsedRange
            $rowCount = [int]$used.Rows.Count
            $columnCount = [int]$used.Columns.Count
            $safeName = ($sheet.Name -replace '[^0-9A-Za-z_-]', '_')
            $csvPath = Join-Path $convertDir ("A2007App2-$<SOURCE_FILE_REDACTED>")
            $writer = New-Object System.IO.StreamWriter($csvPath, $false, $utf8NoBom)
            $values = $used.Value2
            for ($row = 1; $row -le $rowCount; $row++) {
                $fields = for ($column = 1; $column -le $columnCount; $column++) {
                    if ($rowCount -eq 1 -and $columnCount -eq 1) {
                        $value = $values
                    }
                    else {
                        $value = $values[$row, $column]
                    }
                    if ($null -eq $value) {
                        '""'
                    }
                    else {
                        $cellText = [Convert]::ToString($value, $culture).Replace('"', '""')
                        '"' + $cellText + '"'
                    }
                }
                $writer.WriteLine(($fields -join ','))
            }
            $sheetMetadata += [ordered]@{
                sheet_name = $sheet.Name
                csv_file = [System.IO.Path]::GetFileName($csvPath)
                start_row = [int]$used.Row
                start_column = [int]$used.Column
                rows = $rowCount
                columns = $columnCount
            }
        }
        finally {
            if ($null -ne $writer) { $writer.Dispose() }
            if ($null -ne $used) { try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($used) | Out-Null } catch { } }
            if ($null -ne $sheet) { try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($sheet) | Out-Null } catch { } }
        }
    }
    $sheetMetaPath = Join-Path $convertDir 'A2007App2-workbook-metadata.json'
    [System.IO.File]::WriteAllText(
        $sheetMetaPath,
        ($sheetMetadata | ConvertTo-Json -Depth 4),
        $utf8NoBom
    )
}
finally {
    if ($null -ne $workbook) {
        $workbook.Close($false)
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($workbook) | Out-Null
    }
    if ($null -ne $excel) {
        try { $excel.Quit() } catch { }
        try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null } catch { }
    }
}

Get-ChildItem -LiteralPath $convertDir -File |
    Sort-Object Name |
    ForEach-Object {
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
        [PSCustomObject]@{
            file = $_.Name
            bytes = $_.Length
            sha256 = $hash.Hash.ToLowerInvariant()
        }
    }
