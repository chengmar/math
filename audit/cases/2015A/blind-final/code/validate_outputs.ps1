$ErrorActionPreference = 'Stop'
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resultsDir = Join-Path $root 'results'
$figuresDir = Join-Path $root 'figures'
if (-not ('Cumcm2015A.SolarShadowSolver' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'SolarShadow.cs')
}

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
    @{ Name = 'q2_primary'; CandidatePath = '<SOURCE_FILE_REDACTED>'; NormalizedPath = '<SOURCE_FILE_REDACTED>'; Filter = { param($row) $row.branch -eq 'rotation' -and [int]$row.branch_rank -eq 1 } },
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
            check_id = "$($case.Name)_global_rank_$($candidate.global_rmse_rank)_branch_rank_$($candidate.branch_rank)_rmse_recompute"
            status = if ($difference -lt 1.0e-10) { 'pass' } else { 'fail' }
            metric = $difference
            tolerance = 1.0e-10
            detail = ('independent_rmse={0:R}; reported_rmse={1}' -f $computed.Rmse, $candidate.tip_rmse_m)
        }
        $checks += [pscustomobject]@{
            check_id = "$($case.Name)_global_rank_$($candidate.global_rmse_rank)_branch_rank_$($candidate.branch_rank)_sun_above_horizon"
            status = if ($computed.MinAltitude -gt 0.0) { 'pass' } else { 'fail' }
            metric = $computed.MinAltitude
            tolerance = 0.0
            detail = 'Minimum independently recomputed solar altitude in degrees'
        }
    }
}

$manifestRows = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$expectedManifestRows = @(Import-Csv -LiteralPath (Join-Path $PSScriptRoot '<SOURCE_FILE_REDACTED>'))
foreach ($expected in $expectedManifestRows) {
    $row = @($manifestRows | Where-Object relative_path -eq $expected.relative_path)[0]
    $fullPath = Join-Path $root $expected.relative_path
    $actualHash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $checks += [pscustomobject]@{
        check_id = ('immutable_input_hash_' + ([IO.Path]::GetFileName($expected.relative_path)))
        status = if ($null -ne $row -and $actualHash -eq $expected.sha256 -and $row.sha256 -eq $expected.sha256 -and $row.status -eq 'pass') { 'pass' } else { 'fail' }
        metric = $actualHash
        tolerance = 'exact'
        detail = 'SHA256 compared with code/<SOURCE_FILE_REDACTED> before any Office open'
    }
}
$checks += [pscustomobject]@{
    check_id = 'immutable_input_file_set'
    status = if ($manifestRows.Count -eq $expectedManifestRows.Count) { 'pass' } else { 'fail' }
    metric = "$($manifestRows.Count)/$($expectedManifestRows.Count)"
    tolerance = 'exact'
    detail = 'Generated manifest has exactly the immutable expected paths'
}
$inputPreservation = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$checks += [pscustomobject]@{
    check_id = 'source_workbook_preserved_during_excel_extraction'
    status = if ($inputPreservation.Count -eq 1 -and $inputPreservation[0].status -eq 'pass') { 'pass' } else { 'fail' }
    metric = if ($inputPreservation.Count -eq 1) { $inputPreservation[0].after_sha256 } else { 'missing' }
    tolerance = 'before SHA256 equals after SHA256'
    detail = 'Excel opens only a workspace working copy'
}

$holdout = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$separationPass = (@($holdout | Where-Object {
    $_.selection_data -ne 'points_1_14' -or $_.evaluation_data -ne 'points_15_21_final_test' -or $_.final_test_used_for_selection -ne 'fail'
})).Count -eq 0
$checks += [pscustomobject]@{
    check_id = 'fit_evaluate_index_separation'
    status = if ($separationPass) { 'pass' } else { 'fail' }
    metric = "$(@($holdout).Count) rows"
    tolerance = 'exact labels'
    detail = 'Every row declares disjoint 1-14 fitting and 15-21 final testing, with final-test use for selection=fail'
}

$selectionLog = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$selectedRows = @($selectionLog | Where-Object selected_before_final_test -eq 'pass')
$selectionRulePass = $selectedRows.Count -eq 1 -and
    $selectedRows[0].selection_eligible_status -eq 'pass' -and
    $selectedRows[0].candidate -eq 'full_vector_solar_inverse' -and
    (@($selectionLog | Where-Object final_test_used_for_selection -ne 'fail')).Count -eq 0
$checks += [pscustomobject]@{
    check_id = 'training_only_model_selection_rule'
    status = if ($selectionRulePass) { 'pass' } else { 'fail' }
    metric = "$($selectedRows.Count) selected rows"
    tolerance = 'exactly one eligible task-complete candidate selected before final test'
    detail = '<SOURCE_FILE_REDACTED> contains no final-test metric and marks final-test use as fail'
}

$innerPolynomial = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$checks += [pscustomobject]@{
    check_id = 'strong_polynomial_baselines_present'
    status = if ((@($innerPolynomial | Select-Object -ExpandProperty degree -Unique | Sort-Object) -join ',') -eq '1,2,3') { 'pass' } else { 'fail' }
    metric = (@($innerPolynomial | Select-Object -ExpandProperty degree -Unique | Sort-Object) -join ',')
    tolerance = '1,2,3'
    detail = 'Linear, quadratic and cubic trajectory-only counterexample baselines are evaluated inside training data'
}

$q4Synthetic = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$checks += [pscustomobject]@{
    check_id = 'q4_homography_direction_units_normalization'
    status = if ($q4Synthetic.Count -eq 3 -and @($q4Synthetic | Where-Object status -ne 'pass').Count -eq 0) { 'pass' } else { 'fail' }
    metric = "$(@($q4Synthetic | Where-Object status -eq 'pass').Count)/3"
    tolerance = '3/3'
    detail = 'Synthetic pixel-to-ground, ground-to-pixel and homogeneous-normalization tests'
}

$zeroTimes = [double[]](600.0,603.0,606.0)
$zeroCoordinates = [double[]](0.0,0.0,0.0)
$zeroFit = [Cumcm2015A.SolarShadowSolver]::Evaluate($zeroTimes,$zeroCoordinates,$zeroCoordinates,2015,108,1,18.0,110.0,3,$false)
$checks += [pscustomobject]@{
    check_id = 'zero_shadow_degeneracy_rejected'; status = if ($zeroFit.ObjectiveSse -ge 1.0e299) { 'pass' } else { 'fail' }
    metric = $zeroFit.ObjectiveSse; tolerance = 'invalid sentinel'; detail = 'All-zero shadow endpoints cannot produce a perfect H=0 fit'
}
$repeatedTimeRejected = $false
try { [void][Cumcm2015A.SolarShadowSolver]::FitLinear([double[]](600,600,600),[double[]](1,2,3),[double[]](1,2,3),3) } catch { $repeatedTimeRejected = $true }
$checks += [pscustomobject]@{
    check_id = 'repeated_time_baseline_rejected'; status = if ($repeatedTimeRejected) { 'pass' } else { 'fail' }
    metric = $repeatedTimeRejected; tolerance = 'true'; detail = 'Zero time variance raises an explicit error'
}
$night = [Cumcm2015A.SolarShadowSolver]::SolarAt(2015,355,720.0,80.0,0.0)
$checks += [pscustomobject]@{
    check_id = 'night_altitude_and_shadow_valid_separated'; status = if ($night[5] -eq 0.0 -and $night[2] -gt -90.0 -and $night[2] -lt 0.0) { 'pass' } else { 'fail' }
    metric = "altitude=$($night[2]);shadow_valid=$($night[5])"; tolerance = 'true negative altitude and flag=0'; detail = 'Night altitude is not overwritten by -90 degrees'
}
$handednessRejected = $false
try { [void][Cumcm2015A.SolarShadowSolver]::Evaluate($zeroTimes,[double[]](1,1,1),[double[]](1,1,1),2015,108,0,18,110,3,$false) } catch { $handednessRejected = $true }
$checks += [pscustomobject]@{
    check_id = 'handedness_discrete_domain'; status = if ($handednessRejected) { 'pass' } else { 'fail' }
    metric = $handednessRejected; tolerance = 'true'; detail = 'Only {-1,+1} is accepted'
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
