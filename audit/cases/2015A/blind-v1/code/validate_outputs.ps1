$ErrorActionPreference = 'Stop'
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resultsDir = Join-Path $root 'results'
$figuresDir = Join-Path $root 'figures'

function Get-IndependentUnitShadow {
    param(
        [int]$Year, [int]$DayOfYear, [double]$MinuteOfDay,
        [double]$LatitudeDeg, [double]$LongitudeDeg
    )
    $days = if ([DateTime]::IsLeapYear($Year)) { 366.0 } else { 365.0 }
    $gamma = 2.0 * [Math]::PI / $days * ($DayOfYear - 1.0 + ($MinuteOfDay / 60.0 - 12.0) / 24.0)
    $equation = 229.18 * (0.000075 + 0.001868 * [Math]::Cos($gamma) - 0.032077 * [Math]::Sin($gamma) `
        - 0.014615 * [Math]::Cos(2.0 * $gamma) - 0.040849 * [Math]::Sin(2.0 * $gamma))
    $declination = 0.006918 - 0.399912 * [Math]::Cos($gamma) + 0.070257 * [Math]::Sin($gamma) `
        - 0.006758 * [Math]::Cos(2.0 * $gamma) + 0.000907 * [Math]::Sin(2.0 * $gamma) `
        - 0.002697 * [Math]::Cos(3.0 * $gamma) + 0.001480 * [Math]::Sin(3.0 * $gamma)
    $phi = $LatitudeDeg * [Math]::PI / 180.0
    $hourAngle = ($MinuteOfDay + $equation + 4.0 * $LongitudeDeg - 1200.0) * [Math]::PI / 720.0
    $up = [Math]::Sin($phi) * [Math]::Sin($declination) + [Math]::Cos($phi) * [Math]::Cos($declination) * [Math]::Cos($hourAngle)
    if ($up -le 0.0) { return $null }
    [pscustomobject]@{
        East = [Math]::Cos($declination) * [Math]::Sin($hourAngle) / $up
        North = ([Math]::Sin($phi) * [Math]::Cos($declination) * [Math]::Cos($hourAngle) `
            - [Math]::Cos($phi) * [Math]::Sin($declination)) / $up
        AltitudeDeg = [Math]::Asin($up) * 180.0 / [Math]::PI
    }
}

function Recompute-CandidateRmse {
    param($Candidate, [string]$NormalizedCsv)
    $observations = @(Import-Csv -LiteralPath $NormalizedCsv)
    $date = [datetime]::ParseExact($Candidate.date, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    $day = $date.DayOfYear
    $height = [double]$Candidate.inferred_height_m
    $angle = [double]$Candidate.axis_rotation_deg * [Math]::PI / 180.0
    $a = $height * [Math]::Cos($angle)
    $b = $height * [Math]::Sin($angle)
    $sign = [int]$Candidate.handedness
    $sse = 0.0
    $maxError = 0.0
    $minimumAltitude = 90.0
    foreach ($observation in $observations) {
        $unit = Get-IndependentUnitShadow $date.Year $day ([double]$observation.minute_of_day) `
            ([double]$Candidate.latitude_deg) ([double]$Candidate.longitude_deg)
        if ($null -eq $unit) { return [pscustomobject]@{ Rmse = [double]::PositiveInfinity; Max = [double]::PositiveInfinity; MinAltitude = -90.0 } }
        if ($unit.AltitudeDeg -lt $minimumAltitude) { $minimumAltitude = $unit.AltitudeDeg }
        if ($sign -eq 1) {
            $predictedX = $a * $unit.East - $b * $unit.North
            $predictedY = $b * $unit.East + $a * $unit.North
        }
        else {
            $predictedX = $a * $unit.East + $b * $unit.North
            $predictedY = $b * $unit.East - $a * $unit.North
        }
        $dx = [double]$observation.x_m - $predictedX
        $dy = [double]$observation.y_m - $predictedY
        $error = [Math]::Sqrt($dx * $dx + $dy * $dy)
        $sse += $error * $error
        if ($error -gt $maxError) { $maxError = $error }
    }
    [pscustomobject]@{ Rmse = [Math]::Sqrt($sse / $observations.Count); Max = $maxError; MinAltitude = $minimumAltitude }
}

$checks = @()
$q1 = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$q1MaximumIdentityError = 0.0
foreach ($row in $q1) {
    $expected = 3.0 / [Math]::Tan([double]$row.solar_altitude_deg * [Math]::PI / 180.0)
    $difference = [Math]::Abs($expected - [double]$row.shadow_length_m)
    if ($difference -gt $q1MaximumIdentityError) { $q1MaximumIdentityError = $difference }
}
$checks += [pscustomobject]@{
    check_id = 'q1_length_equals_height_cot_altitude'
    status = if ($q1MaximumIdentityError -lt 1.0e-12) { 'pass' } else { 'fail' }
    metric = $q1MaximumIdentityError
    tolerance = 1.0e-12
    detail = 'Independent algebraic identity over every curve row'
}

$candidateCases = @(
    @{ Name = 'q2_primary'; CandidatePath = '<SOURCE_FILE_REDACTED>'; NormalizedPath = '<SOURCE_FILE_REDACTED>'; Filter = { param($row) [int]$row.rank -eq 1 } },
    @{ Name = 'q3_attachment2_all'; CandidatePath = '<SOURCE_FILE_REDACTED>'; NormalizedPath = '<SOURCE_FILE_REDACTED>'; Filter = { param($row) $true } },
    @{ Name = 'q3_attachment3_all'; CandidatePath = '<SOURCE_FILE_REDACTED>'; NormalizedPath = '<SOURCE_FILE_REDACTED>'; Filter = { param($row) $true } }
)
foreach ($case in $candidateCases) {
    $candidates = @(Import-Csv -LiteralPath (Join-Path $resultsDir $case.CandidatePath) |
        Where-Object { & $case.Filter $_ })
    foreach ($candidate in $candidates) {
        $computed = Recompute-CandidateRmse $candidate (Join-Path $resultsDir $case.NormalizedPath)
        $difference = [Math]::Abs($computed.Rmse - [double]$candidate.tip_rmse_m)
        $checks += [pscustomobject]@{
            check_id = "$($case.Name)_rank_$($candidate.rank)_rmse_recompute"
            status = if ($difference -lt 1.0e-10) { 'pass' } else { 'fail' }
            metric = $difference
            tolerance = 1.0e-10
            detail = ('independent_rmse={0:R}; reported_rmse={1}' -f $computed.Rmse, $candidate.tip_rmse_m)
        }
        $checks += [pscustomobject]@{
            check_id = "$($case.Name)_rank_$($candidate.rank)_sun_above_horizon"
            status = if ($computed.MinAltitude -gt 0.0) { 'pass' } else { 'fail' }
            metric = $computed.MinAltitude
            tolerance = 0.0
            detail = 'Minimum independently recomputed solar altitude in degrees'
        }
    }
}

$manifestRows = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
foreach ($row in $manifestRows) {
    $fullPath = Join-Path $root $row.relative_path
    $actualHash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $checks += [pscustomobject]@{
        check_id = ('input_hash_' + ([IO.Path]::GetFileName($row.relative_path)))
        status = if ($actualHash -eq $row.sha256) { 'pass' } else { 'fail' }
        metric = $actualHash
        tolerance = 'exact'
        detail = 'SHA256 compared with generated input manifest'
    }
}

$holdout = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$separationPass = (@($holdout | Where-Object {
    $_.selection_data -ne 'points_1_14' -or $_.evaluation_data -ne 'points_15_21'
})).Count -eq 0
$checks += [pscustomobject]@{
    check_id = 'fit_evaluate_index_separation'
    status = if ($separationPass) { 'pass' } else { 'fail' }
    metric = "$(@($holdout).Count) rows"
    tolerance = 'exact labels'
    detail = 'Every holdout row declares disjoint 1-14 fit and 15-21 evaluation blocks'
}

try { Add-Type -AssemblyName System.Drawing.Common -ErrorAction Stop } catch { Add-Type -AssemblyName System.Drawing }
foreach ($file in (Get-ChildItem -LiteralPath $figuresDir -Filter '*.png' -File)) {
    $image = [System.Drawing.Image]::FromFile($file.FullName)
    try {
        $sizePass = $image.Width -ge 1000 -and $image.Height -ge 600
        $checks += [pscustomobject]@{
            check_id = "figure_size_$($file.BaseName)"
            status = if ($sizePass) { 'pass' } else { 'fail' }
            metric = "$($image.Width)x$($image.Height)"
            tolerance = 'at least 1000x600'
            detail = 'Raster can support paper-scale inspection'
        }
    } finally { $image.Dispose() }
}

$checks += [pscustomobject]@{
    check_id = 'external_geographic_truth'
    status = 'needs_review'
    metric = 'not checked'
    tolerance = 'not applicable'
    detail = 'Solve phase prohibits reference answers; internal recomputation is not external validation.'
}

$checks | Export-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>') -NoTypeInformation -Encoding utf8
$failureCount = @($checks | Where-Object status -eq 'fail').Count
if ($failureCount -gt 0) {
    Write-Host "[FAIL] independent validation found $failureCount failed checks"
    exit 1
}
Write-Host "[PASS] independent validation completed; $($checks.Count) checks, external truth needs_review"
