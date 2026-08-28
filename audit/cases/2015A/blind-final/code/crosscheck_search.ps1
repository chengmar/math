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

$sources = @(
    [pscustomobject]@{ Dataset = '附件1'; Data = '<SOURCE_FILE_REDACTED>'; Candidates = '<SOURCE_FILE_REDACTED>' },
    [pscustomobject]@{ Dataset = '附件2'; Data = '<SOURCE_FILE_REDACTED>'; Candidates = '<SOURCE_FILE_REDACTED>' },
    [pscustomobject]@{ Dataset = '附件3'; Data = '<SOURCE_FILE_REDACTED>'; Candidates = '<SOURCE_FILE_REDACTED>' }
)

$denseCache = @{}
$rows = @()
$publishedCount = 0
foreach ($source in $sources) {
    $data = Read-ObservationArrays (Join-Path $resultsDir $source.Data)
    $candidates = @(Import-Csv -LiteralPath (Join-Path $resultsDir $source.Candidates))
    $publishedCount += $candidates.Count
    foreach ($candidate in $candidates) {
        $date = [datetime]::ParseExact($candidate.date, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
        $allowReflection = [int]$candidate.handedness -eq -1
        $cacheKey = "$($source.Dataset)|$($date.DayOfYear)|$allowReflection"
        if (-not $denseCache.ContainsKey($cacheKey)) {
            $denseCache[$cacheKey] = @([Cumcm2015A.SolarShadowSolver]::SearchKnownDate(
                $data.Time, $data.X, $data.Y, $date.Year, $date.DayOfYear, $data.Time.Count,
                $false, $allowReflection, 64))
        }
        $sameBranch = @($denseCache[$cacheKey] | Where-Object Handedness -eq ([int]$candidate.handedness))
        $matched = @($sameBranch | Sort-Object {
            Angular-SeparationDeg ([double]$candidate.latitude_deg) ([double]$candidate.longitude_deg) $_.LatitudeDeg $_.LongitudeDeg
        })[0]
        $separation = Angular-SeparationDeg ([double]$candidate.latitude_deg) ([double]$candidate.longitude_deg) `
            $matched.LatitudeDeg $matched.LongitudeDeg
        $rmseDifference = [Math]::Abs([double]$candidate.tip_rmse_m - $matched.TipRmseM)
        $rows += [pscustomobject]@{
            case_id = "$($source.Dataset)_global_$($candidate.global_rmse_rank)_branch_$($candidate.branch_rank)"
            branch = $candidate.branch
            handedness = $candidate.handedness
            date = $candidate.date
            profile_latitude_deg = $candidate.latitude_deg
            profile_longitude_deg = $candidate.longitude_deg
            dense_latitude_deg = $matched.LatitudeDeg
            dense_longitude_deg = $matched.LongitudeDeg
            angular_separation_deg = $separation
            profile_rmse_m = $candidate.tip_rmse_m
            dense_rmse_m = $matched.TipRmseM
            rmse_difference_m = $rmseDifference
            status = if ($separation -lt 0.01 -and $rmseDifference -lt 1.0e-8) { 'pass' } else { 'fail' }
        }
    }
}

$rows | Export-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>') -NoTypeInformation -Encoding utf8
if ($rows.Count -ne $publishedCount -or @($rows | Where-Object status -eq 'fail').Count -gt 0) {
    Write-Host "[FAIL] dense cross-check coverage=$($rows.Count)/$publishedCount"
    exit 1
}
Write-Host "[PASS] dense global-search cross-checks cover all $publishedCount published rotation and reflection candidates"
