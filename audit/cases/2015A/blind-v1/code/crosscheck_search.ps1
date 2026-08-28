$ErrorActionPreference = 'Stop'
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resultsDir = Join-Path $root 'results'
if (-not ('Cumcm2015A.SolarShadowSolver' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'SolarShadow.cs')
}

function Read-ObservationArrays {
    param([string]$Path)
    $rows = @(Import-Csv -LiteralPath $Path)
    [pscustomobject]@{
        Time = [double[]]@($rows | ForEach-Object { [double]$_.minute_of_day })
        X = [double[]]@($rows | ForEach-Object { [double]$_.x_m })
        Y = [double[]]@($rows | ForEach-Object { [double]$_.y_m })
    }
}

function Angular-SeparationDeg {
    param([double]$LatitudeA, [double]$LongitudeA, [double]$LatitudeB, [double]$LongitudeB)
    $dLat = $LatitudeA - $LatitudeB
    $dLon = ($LongitudeA - $LongitudeB) % 360.0
    if ($dLon -ge 180.0) { $dLon -= 360.0 }
    if ($dLon -lt -180.0) { $dLon += 360.0 }
    $scaledLon = $dLon * [Math]::Cos(0.5 * ($LatitudeA + $LatitudeB) * [Math]::PI / 180.0)
    [Math]::Sqrt($dLat * $dLat + $scaledLon * $scaledLon)
}

$cases = @(
    [pscustomobject]@{ Name = 'q2_primary'; Data = '<SOURCE_FILE_REDACTED>'; Candidates = '<SOURCE_FILE_REDACTED>'; Rank = 1 },
    [pscustomobject]@{ Name = 'q3_attachment2_branch1'; Data = '<SOURCE_FILE_REDACTED>'; Candidates = '<SOURCE_FILE_REDACTED>'; Rank = 1 },
    [pscustomobject]@{ Name = 'q3_attachment2_branch2'; Data = '<SOURCE_FILE_REDACTED>'; Candidates = '<SOURCE_FILE_REDACTED>'; Rank = 2 },
    [pscustomobject]@{ Name = 'q3_attachment3_branch1'; Data = '<SOURCE_FILE_REDACTED>'; Candidates = '<SOURCE_FILE_REDACTED>'; Rank = 1 },
    [pscustomobject]@{ Name = 'q3_attachment3_branch2'; Data = '<SOURCE_FILE_REDACTED>'; Candidates = '<SOURCE_FILE_REDACTED>'; Rank = 2 }
)

$rows = foreach ($case in $cases) {
    $data = Read-ObservationArrays (Join-Path $resultsDir $case.Data)
    $candidate = @(Import-Csv -LiteralPath (Join-Path $resultsDir $case.Candidates) |
        Where-Object { [int]$_.rank -eq $case.Rank })[0]
    $date = [datetime]::ParseExact($candidate.date, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    $dense = @([Cumcm2015A.SolarShadowSolver]::SearchKnownDate(
        $data.Time, $data.X, $data.Y, $date.Year, $date.DayOfYear, $data.Time.Count,
        $false, $false, 1))[0]
    $separation = Angular-SeparationDeg ([double]$candidate.latitude_deg) ([double]$candidate.longitude_deg) `
        $dense.LatitudeDeg $dense.LongitudeDeg
    $rmseDifference = [Math]::Abs([double]$candidate.tip_rmse_m - $dense.TipRmseM)
    [pscustomobject]@{
        case_id = $case.Name
        date = $candidate.date
        profile_latitude_deg = $candidate.latitude_deg
        profile_longitude_deg = $candidate.longitude_deg
        dense_latitude_deg = $dense.LatitudeDeg
        dense_longitude_deg = $dense.LongitudeDeg
        angular_separation_deg = $separation
        profile_rmse_m = $candidate.tip_rmse_m
        dense_rmse_m = $dense.TipRmseM
        rmse_difference_m = $rmseDifference
        status = if ($separation -lt 0.01 -and $rmseDifference -lt 1.0e-8) { 'pass' } else { 'fail' }
    }
}

$rows | Export-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>') -NoTypeInformation -Encoding utf8
if (@($rows | Where-Object status -eq 'fail').Count -gt 0) {
    Write-Host '[FAIL] one or more dense global-search cross-checks disagree'
    exit 1
}
Write-Host '[PASS] dense global-search cross-checks agree with all retained rotation branches'
