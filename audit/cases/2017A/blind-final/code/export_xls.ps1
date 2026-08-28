param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot),
    [string]$SourceXls,
    [string]$OutputRoot
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

$workspaceRoot = if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    [System.IO.Path]::GetFullPath($Workspace)
} else {
    [System.IO.Path]::GetFullPath($OutputRoot)
}
$source = if ([string]::IsNullOrWhiteSpace($SourceXls)) {
    Assert-InWorkspace `
        -Candidate (Join-Path $workspaceRoot 'input\data\<SOURCE_FILE_REDACTED>') `
        -Root $workspaceRoot
} else {
    [System.IO.Path]::GetFullPath($SourceXls)
}
$outputDirectory = Assert-InWorkspace `
    -Candidate (Join-Path $workspaceRoot 'results\normalized') `
    -Root $workspaceRoot
$destination = Assert-InWorkspace `
    -Candidate (Join-Path $outputDirectory '<SOURCE_FILE_REDACTED>') `
    -Root $workspaceRoot
$stagingSource = Assert-InWorkspace `
    -Candidate (Join-Path $outputDirectory '<SOURCE_FILE_REDACTED>') `
    -Root $workspaceRoot

if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Input workbook is missing: $source"
}
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $stagingSource -Force

$excel = $null
$workbook = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    # Excel may update OLE bookkeeping even when a workbook is opened read-only.
    # Open only a workspace-local staging copy so the supplied raw input is never touched.
    $workbook = $excel.Workbooks.Open($stagingSource, 0, $true)

    $sheetMetadata = @()
    for ($index = 1; $index -le $workbook.Worksheets.Count; $index++) {
        $sheet = $workbook.Worksheets.Item($index)
        $usedRange = $sheet.UsedRange
        $sheetMetadata += [PSCustomObject]@{
            name = $sheet.Name
            rows = [int]$usedRange.Rows.Count
            columns = [int]$usedRange.Columns.Count
        }
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($usedRange)
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($sheet)
    }

    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        Remove-Item -LiteralPath $destination -Force
    }
    # 51 = xlOpenXMLWorkbook (.xlsx). The source workbook remains read-only.
    $workbook.SaveAs($destination, 51)

    [PSCustomObject]@{
        status = 'pass'
        source = $source
        destination = $destination
        excel_version = $excel.Version
        sheets = $sheetMetadata
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
    if ($null -ne $workbook) {
        $workbook.Close($false)
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if (Test-Path -LiteralPath $stagingSource -PathType Leaf) {
        Remove-Item -LiteralPath $stagingSource -Force
    }
}
