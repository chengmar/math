param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace
)

$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$dataDir = Join-Path $workspacePath 'input\data'
$resultDir = Join-Path $workspacePath 'results'
if (-not (Test-Path -LiteralPath $resultDir)) {
    New-Item -ItemType Directory -Path $resultDir | Out-Null
}

$xlsFiles = @(Get-ChildItem -LiteralPath $dataDir -File -Filter '*.xls')
if ($xlsFiles.Count -ne 1) {
    throw "<SOURCE_FILE_REDACTED> input, found $($xlsFiles.Count)."
}

$sourcePath = $xlsFiles[0].FullName
$sourcePreHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
$sourcePreLength = (Get-Item -LiteralPath $sourcePath).Length
$tempDir = Join-Path $resultDir ('.excel-copy-' + [System.IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $tempDir | Out-Null
$workingCopy = Join-Path $tempDir $xlsFiles[0].Name
Copy-Item -LiteralPath $sourcePath -Destination $workingCopy
$copyPreHash = (Get-FileHash -LiteralPath $workingCopy -Algorithm SHA256).Hash.ToLowerInvariant()

$excel = $null
$book = $null
$sheet = $null
$used = $null
$excelVersion = $null
$records = $null
$conditions = $null
$sheetName = $null
try {
    try {
        # Excel is allowed to touch only this disposable copy.  The original is never opened.
        $excel = New-Object -ComObject Excel.Application
        $excelVersion = [string]$excel.Version
        $excel.Visible = $false
        $excel.DisplayAlerts = $false
        $book = $excel.Workbooks.Open($workingCopy, 0, $true)
        $sheet = $book.Worksheets.Item(1)
        $sheetName = [string]$sheet.Name
        $used = $sheet.UsedRange
        $values = $used.Value2

        $rowLo = $values.GetLowerBound(0)
        $rowHi = $values.GetUpperBound(0)
        $colHi = $values.GetUpperBound(1)
        $headerRow = $null
        for ($r = $rowLo; $r -lt $rowHi; $r++) {
            if ($colHi -lt 4) { break }
            $nextRow = $r + 1
            $headerTorque = $values[$r, 2]
            $headerSpeed = $values[$r, 3]
            $headerTime = $values[$r, 4]
            $dataTorque = $values[$nextRow, 2]
            $dataSpeed = $values[$nextRow, 3]
            $dataTime = $values[$nextRow, 4]
            if (-not [string]::IsNullOrWhiteSpace([string]$headerTorque) -and
                -not [string]::IsNullOrWhiteSpace([string]$headerSpeed) -and
                -not [string]::IsNullOrWhiteSpace([string]$headerTime) -and
                $dataTorque -is [double] -and
                $dataSpeed -is [double] -and
                $dataTime -is [double]) {
                $headerRow = $r
                break
            }
        }
        if ($null -eq $headerRow) {
            throw 'Could not locate the torque/speed/time header row.'
        }

        $records = [System.Collections.Generic.List[object]]::new()
        for ($r = $headerRow + 1; $r -le $rowHi; $r++) {
            $torque = $values[$r, 2]
            $speed = $values[$r, 3]
            $time = $values[$r, 4]
            if ($null -eq $torque -or $null -eq $speed -or $null -eq $time -or
                [string]::IsNullOrWhiteSpace([string]$torque) -or
                [string]::IsNullOrWhiteSpace([string]$speed) -or
                [string]::IsNullOrWhiteSpace([string]$time)) {
                if ($records.Count -gt 0) { break }
                continue
            }
            $records.Add([pscustomobject]@{
                time_s = [double]$time
                brake_torque_Nm = [double]$torque
                speed_rpm = [double]$speed
            })
        }
        if ($records.Count -lt 2) {
            throw "Only $($records.Count) usable observations were extracted."
        }

        $firstDataRow = $headerRow + 1
        $conditions = [ordered]@{}
        for ($c = 5; $c -le [Math]::Min(8, $colHi); $c++) {
            $key = [string]($values[$headerRow, $c])
            if (-not [string]::IsNullOrWhiteSpace($key)) {
                $conditions[$key] = [double]$values[$firstDataRow, $c]
            }
        }
    }
    finally {
        if ($book) { $book.Close($false) }
        if ($excel) { $excel.Quit() }
        foreach ($obj in @($used, $sheet, $book, $excel)) {
            if ($null -ne $obj) {
                [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($obj)
            }
        }
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }

    $copyPostHash = (Get-FileHash -LiteralPath $workingCopy -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourcePostHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourcePostLength = (Get-Item -LiteralPath $sourcePath).Length
    $sourceStatus = if ($sourcePreHash -eq $sourcePostHash -and $sourcePreLength -eq $sourcePostLength) { 'pass' } else { 'fail' }
    $copyStatus = if ($copyPreHash -eq $copyPostHash) { 'pass' } else { 'fail' }

    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('time_s,brake_torque_Nm,speed_rpm')
    foreach ($record in $records) {
        $lines.Add(($record.time_s.ToString('R', $culture) + ',' +
                    $record.brake_torque_Nm.ToString('R', $culture) + ',' +
                    $record.speed_rpm.ToString('R', $culture)))
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $csvPath = Join-Path $resultDir '<SOURCE_FILE_REDACTED>'
    [System.IO.File]::WriteAllText($csvPath, ([string]::Join("`n", $lines) + "`n"), $utf8NoBom)
    $semanticHash = (Get-FileHash -LiteralPath $csvPath -Algorithm SHA256).Hash.ToLowerInvariant()

    $metadata = [ordered]@{
        status = $sourceStatus
        source_file = 'input/data/' + $xlsFiles[0].Name
        source_pre_open_sha256 = $sourcePreHash
        source_post_extraction_sha256 = $sourcePostHash
        source_pre_length_bytes = $sourcePreLength
        source_post_length_bytes = $sourcePostLength
        source_byte_integrity_status = $sourceStatus
        source_isolation_status = 'pass'
        source_isolation_detail = 'The original was never opened; Excel received only a disposable copy.'
        working_copy_pre_open_sha256 = $copyPreHash
        working_copy_post_open_sha256 = $copyPostHash
        working_copy_byte_stability_status = $copyStatus
        normalized_observations_sha256 = $semanticHash
        normalized_encoding = 'UTF-8 without BOM, LF, invariant round-trip numeric formatting'
        sheet = $sheetName
        extracted_rows = $records.Count
        columns = @('time_s', 'brake_torque_Nm', 'speed_rpm')
        conditions = $conditions
        powershell_version = $PSVersionTable.PSVersion.ToString()
        excel_version = $excelVersion
        note = 'Blank formatted rows after the contiguous numeric block are excluded.'
    }
    $jsonText = ($metadata | ConvertTo-Json -Depth 6) -replace "`r`n", "`n"
    [System.IO.File]::WriteAllText((Join-Path $resultDir 'input-metadata.json'), ($jsonText + "`n"), $utf8NoBom)

    if ($sourceStatus -ne 'pass') {
        throw 'Original input bytes changed during extraction.'
    }
    Write-Output "[pass] extracted $($records.Count) observations; original input hash stayed $sourcePreHash"
}
finally {
    if (Test-Path -LiteralPath $workingCopy) { Remove-Item -LiteralPath $workingCopy -Force }
    if (Test-Path -LiteralPath $tempDir) { Remove-Item -LiteralPath $tempDir -Force }
}
