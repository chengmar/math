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

$excel = $null
$book = $null
$sheet = $null
$used = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $book = $excel.Workbooks.Open($xlsFiles[0].FullName, 0, $true)
    $sheet = $book.Worksheets.Item(1)
    $used = $sheet.UsedRange
    $values = $used.Value2

    $rowLo = $values.GetLowerBound(0)
    $rowHi = $values.GetUpperBound(0)
    $colHi = $values.GetUpperBound(1)
    $headerRow = $null
    for ($r = $rowLo; $r -lt $rowHi; $r++) {
        $headerTorque = $values[$r, 2]
        $headerSpeed = $values[$r, 3]
        $headerTime = $values[$r, 4]
        $nextRow = $r + 1
        $dataTorque = $values[$nextRow, 2]
        $dataSpeed = $values[$nextRow, 3]
        $dataTime = $values[$nextRow, 4]
        if ($colHi -ge 4 -and
            -not [string]::IsNullOrWhiteSpace([string]$headerTorque) -and
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

    $csvPath = Join-Path $resultDir '<SOURCE_FILE_REDACTED>'
    $records | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding utf8

    $firstDataRow = $headerRow + 1
    $conditions = [ordered]@{}
    for ($c = 5; $c -le [Math]::Min(8, $colHi); $c++) {
        $key = [string]($values[$headerRow, $c])
        if (-not [string]::IsNullOrWhiteSpace($key)) {
            $conditions[$key] = [double]($values[$firstDataRow, $c])
        }
    }
    $metadata = [ordered]@{
        status = 'pass'
        source_file = 'input/data/' + $xlsFiles[0].Name
        source_sha256 = (Get-FileHash -LiteralPath $xlsFiles[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        sheet = [string]$sheet.Name
        extracted_rows = $records.Count
        columns = @('time_s', 'brake_torque_Nm', 'speed_rpm')
        conditions = $conditions
        note = 'Blank formatted rows after the contiguous numeric block are excluded.'
    }
    $metadata | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $resultDir 'input-metadata.json') -Encoding utf8
    Write-Output "[pass] extracted $($records.Count) observations from sheet '$($sheet.Name)'"
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
