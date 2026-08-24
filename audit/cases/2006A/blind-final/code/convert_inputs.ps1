param(
    [string]$Workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Workspace = (Resolve-Path -LiteralPath $Workspace).Path
$inputRoot = Join-Path $Workspace 'input'
$finalConvertedRoot = Join-Path $inputRoot 'converted'
$transactionId = [guid]::NewGuid().ToString('N')
$convertedRoot = Join-Path $inputRoot ("converted-staging-$transactionId")
$backupRoot = Join-Path $inputRoot ("converted-backup-$transactionId")
$wordRoot = Join-Path $convertedRoot 'word'
$excelRoot = Join-Path $convertedRoot 'excel'
$resolvedWorkspace = [System.IO.Path]::GetFullPath($Workspace).TrimEnd('\')
$resolvedStagingRoot = [System.IO.Path]::GetFullPath($convertedRoot).TrimEnd('\')
$resolvedFinalRoot = [System.IO.Path]::GetFullPath($finalConvertedRoot).TrimEnd('\')
$resolvedBackupRoot = [System.IO.Path]::GetFullPath($backupRoot).TrimEnd('\')
$expectedFinalRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedWorkspace 'input\converted')).TrimEnd('\')
foreach ($candidateRoot in @($resolvedStagingRoot, $resolvedFinalRoot, $resolvedBackupRoot)) {
    if (-not $candidateRoot.StartsWith($resolvedWorkspace + '\input\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe generated-output path: $candidateRoot"
    }
}
if ($resolvedFinalRoot -ne $expectedFinalRoot) {
    throw "Unexpected final conversion path: $resolvedFinalRoot"
}

$conversionSucceeded = $false
try {
    New-Item -ItemType Directory -Force -Path $wordRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $excelRoot | Out-Null

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $invariant = [System.Globalization.CultureInfo]::InvariantCulture
    $manifest = [ordered]@{
        generator = 'code/convert_inputs.ps1'
        generated_at = (Get-Date).ToString('o')
        word = @()
        excel = @()
        formula_audit = @()
    }

function Release-ComObject {
    param([object]$Object)
    if ($null -ne $Object -and [System.Runtime.InteropServices.Marshal]::IsComObject($Object)) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Object)
    }
}

function Convert-ToCsvField {
    param([object]$Value)

    if ($null -eq $Value) {
        $textValue = ''
    }
    elseif ($Value -is [double] -or $Value -is [single] -or
            $Value -is [decimal] -or $Value -is [int] -or
            $Value -is [long] -or $Value -is [short]) {
        $textValue = ([System.IFormattable]$Value).ToString($null, $invariant)
    }
    elseif ($Value -is [datetime]) {
        $textValue = $Value.ToString('o', $invariant)
    }
    else {
        $textValue = [string]$Value
    }

    return '"' + $textValue.Replace('"', '""') + '"'
}

$wordSources = @(
    (Join-Path $inputRoot 'problem\<SOURCE_FILE_REDACTED>'),
    (Join-Path $inputRoot 'extracted\A2006data\<SOURCE_FILE_REDACTED>')
)

# Word 12 can terminate its COM server while closing some legacy documents.
# Use an isolated process per file and treat cleanup RPC errors as harmless only
# after the text has been written successfully.
foreach ($source in $wordSources) {
    $word = $null
    $document = $null
    $range = $null
    $written = $false
    try {
        $word = New-Object -ComObject Word.Application
        $word.Visible = $false
        $word.DisplayAlerts = 0
        $document = $<SOURCE_FILE_REDACTED>uments.Open($source, $false, $true)
        $range = $document.Content
        $textValue = [string]$range.Text
        $textValue = $textValue.Replace([string][char]7, "`t")
        $textValue = $textValue.Replace("`r", "`n")
        $outputName = ([System.IO.Path]::GetFileNameWithoutExtension($source)) + '.txt'
        $outputPath = Join-Path $wordRoot $outputName
        [System.IO.File]::WriteAllText($outputPath, $textValue, $utf8NoBom)
        $written = $true
        $manifest.word += [ordered]@{
            source = $source.Substring($Workspace.Length + 1).Replace('\', '/')
            output = $outputPath.Substring($Workspace.Length + 1).Replace('\', '/')
            characters = $textValue.Length
        }
    }
    finally {
        Release-ComObject $range
        if ($null -ne $document) {
            try { $document.Close($false) } catch { if (-not $written) { throw } }
            Release-ComObject $document
        }
        if ($null -ne $word) {
            try { $word.Quit() } catch { if (-not $written) { throw } }
            Release-ComObject $word
        }
        [gc]::Collect()
        [gc]::WaitForPendingFinalizers()
    }
}

$excel = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false

    $excelSources = Get-ChildItem -LiteralPath (Join-Path $inputRoot 'extracted\A2006data') -Filter '*.xls' -File |
        Sort-Object Name

    foreach ($sourceFile in $excelSources) {
        $workbook = $null
        $workbookOutputRoot = Join-Path $excelRoot $sourceFile.BaseName
        New-Item -ItemType Directory -Force -Path $workbookOutputRoot | Out-Null

        try {
            $workbook = $excel.Workbooks.Open($sourceFile.FullName, 0, $true)
            for ($sheetIndex = 1; $sheetIndex -le $workbook.Worksheets.Count; $sheetIndex++) {
                $worksheet = $null
                $range = $null
                $singleSheetWorkbook = $null
                try {
                    $worksheet = $workbook.Worksheets.Item($sheetIndex)
                    $range = $worksheet.UsedRange
                    $rowCount = [int]$range.Rows.Count
                    $columnCount = [int]$range.Columns.Count

                    if ($sourceFile.BaseName -like '附件4_*') {
                        $currentCategory = ''
                        for ($auditRow = 1; $auditRow -le $rowCount; $auditRow++) {
                            $categoryCell = $null
                            $courseCell = $null
                            $requestCell = $null
                            try {
                                $categoryCell = $worksheet.Cells.Item($auditRow, 1)
                                $courseCell = $worksheet.Cells.Item($auditRow, 2)
                                $requestCell = $worksheet.Cells.Item($auditRow, 9)
                                $categoryText = [string]$categoryCell.Text
                                $courseText = [string]$courseCell.Text
                                if ($categoryText -and $categoryText -notin @('数据说明', '学科名称')) {
                                    $currentCategory = $categoryText
                                }
                                if ($courseText -eq '总计') {
                                    $formulaText = [string]$requestCell.Formula
                                    $manifest.formula_audit += [ordered]@{
                                        source = $sourceFile.FullName.Substring($Workspace.Length + 1).Replace('\', '/')
                                        sheet_name = [string]$worksheet.Name
                                        row = $auditRow
                                        category = $currentCategory
                                        request_value = [string]$requestCell.Text
                                        request_formula = $formulaText
                                        request_cell_kind = if ($formulaText.StartsWith('=')) { 'formula' } else { 'hardcoded' }
                                    }
                                }
                            }
                            finally {
                                Release-ComObject $requestCell
                                Release-ComObject $courseCell
                                Release-ComObject $categoryCell
                            }
                        }
                    }

                    $safeSheetName = ([string]$worksheet.Name) -replace '[\\/:*?"<>|]', '_'
                    $outputName = ('{0:D2}_{1}.tsv' -f $sheetIndex, $safeSheetName)
                    $outputPath = Join-Path $workbookOutputRoot $outputName

                    # Copying one sheet to a temporary workbook lets Excel's
                    # native writer export the full block in one operation.
                    # File format 42 is Unicode text (UTF-16, tab delimited).
                    $worksheet.Copy()
                    $singleSheetWorkbook = $excel.ActiveWorkbook
                    $singleSheetWorkbook.SaveAs($outputPath, 42)
                    $singleSheetWorkbook.Close($false)
                    Release-ComObject $singleSheetWorkbook
                    $singleSheetWorkbook = $null

                    $manifest.excel += [ordered]@{
                        source = $sourceFile.FullName.Substring($Workspace.Length + 1).Replace('\', '/')
                        sheet_index = $sheetIndex
                        sheet_name = [string]$worksheet.Name
                        output = $outputPath.Substring($Workspace.Length + 1).Replace('\', '/')
                        rows = $rowCount
                        columns = $columnCount
                    }
                }
                finally {
                    if ($null -ne $singleSheetWorkbook) {
                        try { $singleSheetWorkbook.Close($false) } catch { }
                        Release-ComObject $singleSheetWorkbook
                    }
                    Release-ComObject $range
                    Release-ComObject $worksheet
                }
            }
        }
        finally {
            if ($null -ne $workbook) {
                $workbook.Close($false)
                Release-ComObject $workbook
            }
        }
    }
}
finally {
    if ($null -ne $excel) {
        $excel.Quit()
        Release-ComObject $excel
    }
    [gc]::Collect()
    [gc]::WaitForPendingFinalizers()
}

    $manifestPath = Join-Path $convertedRoot 'manifest.json'
    $manifestJson = $manifest | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8NoBom)

    if (Test-Path -LiteralPath $resolvedBackupRoot) {
        throw "Unexpected pre-existing transaction backup: $resolvedBackupRoot"
    }
    if (Test-Path -LiteralPath $resolvedFinalRoot) {
        Move-Item -LiteralPath $resolvedFinalRoot -Destination $resolvedBackupRoot
    }
    try {
        Move-Item -LiteralPath $resolvedStagingRoot -Destination $resolvedFinalRoot
    }
    catch {
        if (-not (Test-Path -LiteralPath $resolvedFinalRoot) -and
            (Test-Path -LiteralPath $resolvedBackupRoot)) {
            Move-Item -LiteralPath $resolvedBackupRoot -Destination $resolvedFinalRoot
        }
        throw
    }
    $conversionSucceeded = $true
    if (Test-Path -LiteralPath $resolvedBackupRoot) {
        Remove-Item -LiteralPath $resolvedBackupRoot -Recurse -Force
    }
    Write-Output $manifestJson
}
finally {
    if (-not $conversionSucceeded -and (Test-Path -LiteralPath $resolvedStagingRoot)) {
        Remove-Item -LiteralPath $resolvedStagingRoot -Recurse -Force
    }
}
