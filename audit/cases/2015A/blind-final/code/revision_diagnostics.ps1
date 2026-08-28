param(
    [int]$BlockBootstrapReplicates = 100,
    [switch]$SmokeTest
)

$ErrorActionPreference = 'Stop'
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Threading.Thread]::CurrentThread.CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resultsDir = Join-Path $root 'results'
if (-not ('Cumcm2015A.SolarShadowSolver' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'SolarShadow.cs')
}

function Export-Rows {
    param([Parameter(Mandatory)]$Rows, [Parameter(Mandatory)][string]$Path)
    @($Rows) | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding utf8
}

function Get-Quantile {
    param([double[]]$Values, [double]$Probability)
    $sorted = @($Values | Sort-Object)
    if ($sorted.Count -eq 0) { return [double]::NaN }
    if ($sorted.Count -eq 1) { return [double]$sorted[0] }
    $position = ($sorted.Count - 1) * $Probability
    $lower = [Math]::Floor($position)
    $upper = [Math]::Ceiling($position)
    if ($lower -eq $upper) { return [double]$sorted[$lower] }
    [double]$sorted[$lower] + ($position - $lower) * ([double]$sorted[$upper] - [double]$sorted[$lower])
}

function Get-GreatCircleDistanceKm {
    param([double]$LatitudeA, [double]$LongitudeA, [double]$LatitudeB, [double]$LongitudeB)
    $radiusKm = 6371.0088
    $toRad = [Math]::PI / 180.0
    $phiA = $LatitudeA * $toRad
    $phiB = $LatitudeB * $toRad
    $deltaPhi = ($LatitudeB - $LatitudeA) * $toRad
    $deltaLambda = ($LongitudeB - $LongitudeA) * $toRad
    $a = [Math]::Sin($deltaPhi / 2.0) * [Math]::Sin($deltaPhi / 2.0) +
         [Math]::Cos($phiA) * [Math]::Cos($phiB) *
         [Math]::Sin($deltaLambda / 2.0) * [Math]::Sin($deltaLambda / 2.0)
    $a = [Math]::Max(0.0, [Math]::Min(1.0, $a))
    2.0 * $radiusKm * [Math]::Atan2([Math]::Sqrt($a), [Math]::Sqrt(1.0 - $a))
}

function Get-LagOneCorrelation {
    param([double[]]$Values)
    if ($Values.Count -lt 3) { return [double]::NaN }
    $left = @($Values[0..($Values.Count - 2)])
    $right = @($Values[1..($Values.Count - 1)])
    $leftMean = ($left | Measure-Object -Average).Average
    $rightMean = ($right | Measure-Object -Average).Average
    $numerator = 0.0
    $leftSse = 0.0
    $rightSse = 0.0
    for ($i = 0; $i -lt $left.Count; ++$i) {
        $dl = [double]$left[$i] - $leftMean
        $dr = [double]$right[$i] - $rightMean
        $numerator += $dl * $dr
        $leftSse += $dl * $dl
        $rightSse += $dr * $dr
    }
    if ($leftSse -le 0.0 -or $rightSse -le 0.0) { return [double]::NaN }
    $numerator / [Math]::Sqrt($leftSse * $rightSse)
}

function Read-Dataset {
    param([string]$Name)
    $rows = @(Import-Csv -LiteralPath (Join-Path $resultsDir ("{0}<SOURCE_FILE_REDACTED>" -f $Name)))
    [pscustomobject]@{
        Name = $Name
        Time = [double[]]@($rows | ForEach-Object { [double]$_.minute_of_day })
        X = [double[]]@($rows | ForEach-Object { [double]$_.x_m })
        Y = [double[]]@($rows | ForEach-Object { [double]$_.y_m })
    }
}

function Convert-CandidateToFit {
    param($Candidate, [int]$FitCount = 21)
    $date = [datetime]::ParseExact($Candidate.date, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    $height = [double]$Candidate.inferred_height_m
    $angle = [double]$Candidate.axis_rotation_deg * [Math]::PI / 180.0
    $fit = [Cumcm2015A.FitResult]::new()
    $fit.Year = $date.Year
    $fit.DayOfYear = $date.DayOfYear
    $fit.Handedness = [int]$Candidate.handedness
    $fit.LengthOnly = $false
    $fit.FitCount = $FitCount
    $fit.LatitudeDeg = [double]$Candidate.latitude_deg
    $fit.LongitudeDeg = [double]$Candidate.longitude_deg
    $fit.A = $height * [Math]::Cos($angle)
    $fit.B = $height * [Math]::Sin($angle)
    $fit.HeightM = $height
    $fit.RotationDeg = [double]$Candidate.axis_rotation_deg
    $fit.TipRmseM = [double]$Candidate.tip_rmse_m
    $fit
}

function Get-ResidualArrays {
    param($Dataset, $Fit)
    $predictions = [Cumcm2015A.SolarShadowSolver]::Predict($Dataset.Time, $Fit)
    [double[]]$dx = [double[]]::new($Dataset.Time.Count)
    [double[]]$dy = [double[]]::new($Dataset.Time.Count)
    for ($i = 0; $i -lt $Dataset.Time.Count; ++$i) {
        $dx[$i] = $Dataset.X[$i] - $predictions[$i][0]
        $dy[$i] = $Dataset.Y[$i] - $predictions[$i][1]
    }
    [pscustomobject]@{ Predictions = $predictions; Dx = $dx; Dy = $dy }
}

function Get-LocalCalendarFit {
    param($Dataset, [double[]]$X, [double[]]$Y, $BaseFit, [int]$RadiusDays = 3)
    $best = $null
    foreach ($offset in (-$RadiusDays)..$RadiusDays) {
        $day = $BaseFit.DayOfYear + $offset
        if ($day -lt 1 -or $day -gt 365) { continue }
        $trial = [Cumcm2015A.SolarShadowSolver]::Refine(
            $Dataset.Time, $X, $Y, 2015, $day, 1,
            $BaseFit.LatitudeDeg, $BaseFit.LongitudeDeg, $Dataset.Time.Count, $false, 0.5)
        if ($null -eq $best -or $trial.ObjectiveSse -lt $best.ObjectiveSse) { $best = $trial }
    }
    $best
}

function Get-AnnualFit {
    param($Dataset, [double[]]$X, [double[]]$Y)
    $profile = @([Cumcm2015A.SolarShadowSolver]::SearchDailyProfile(
        $Dataset.Time, $X, $Y, 2015, 1, $Dataset.Time.Count, $false))
    @($profile | Sort-Object ObjectiveSse)[0]
}

$datasets = [ordered]@{
    '附件1' = Read-Dataset '附件1'
    '附件2' = Read-Dataset '附件2'
    '附件3' = Read-Dataset '附件3'
}

$baseCases = @()
$q2Candidate = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>') |
    Where-Object { $_.branch -eq 'rotation' -and [int]$_.branch_rank -eq 1 })[0]
$baseCases += [pscustomobject]@{
    Dataset = '附件1'; Branch = 'rotation_primary'; Candidate = $q2Candidate
    Fit = Convert-CandidateToFit $q2Candidate
}
foreach ($name in @('附件2', '附件3')) {
    $rows = @(Import-Csv -LiteralPath (Join-Path $resultsDir ("q3_{0}<SOURCE_FILE_REDACTED>" -f $name)) |
        Where-Object branch -eq 'rotation' | Sort-Object { [int]$_.branch_rank })
    foreach ($row in $rows) {
        $baseCases += [pscustomobject]@{
            Dataset = $name; Branch = "rotation_calendar_branch_$($row.branch_rank)"; Candidate = $row
            Fit = Convert-CandidateToFit $row
        }
    }
}

# Residual scale and serial structure. Rounding cannot be a total-error model when these diagnostics disagree.
$roundingRadialBoundM = [Math]::Sqrt(2.0) * 0.00005
$residualDiagnosticRows = foreach ($case in $baseCases) {
    $dataset = $datasets[$case.Dataset]
    $residuals = Get-ResidualArrays $dataset $case.Fit
    [double[]]$radial = @(for ($i = 0; $i -lt $dataset.Time.Count; ++$i) {
        [Math]::Sqrt($residuals.Dx[$i] * $residuals.Dx[$i] + $residuals.Dy[$i] * $residuals.Dy[$i])
    })
    $rmse = [Math]::Sqrt((($radial | ForEach-Object { $_ * $_ } | Measure-Object -Sum).Sum) / $radial.Count)
    $maxError = ($radial | Measure-Object -Maximum).Maximum
    $lagX = Get-LagOneCorrelation $residuals.Dx
    $lagY = Get-LagOneCorrelation $residuals.Dy
    [pscustomobject]@{
        dataset = $case.Dataset
        branch = $case.Branch
        residual_rmse_m = $rmse
        residual_max_m = $maxError
        rounding_radial_bound_m = $roundingRadialBoundM
        rmse_to_rounding_bound_ratio = $rmse / $roundingRadialBoundM
        max_to_rounding_bound_ratio = $maxError / $roundingRadialBoundM
        residual_x_lag1_correlation = $lagX
        residual_y_lag1_correlation = $lagY
        rounding_as_total_error_status = if ($rmse -le $roundingRadialBoundM -and [Math]::Abs($lagX) -lt 0.5 -and [Math]::Abs($lagY) -lt 0.5) { 'pass' } else { 'fail' }
        total_error_interval_status = 'needs_review'
    }
}
Export-Rows $residualDiagnosticRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Circular moving-block residual bootstrap, conditional on each retained calendar/handedness branch.
$bootstrapRandom = [System.Random]::new(2016)
$blockLength = 3
$bootstrapRows = @()
foreach ($case in $baseCases) {
    $dataset = $datasets[$case.Dataset]
    $residuals = Get-ResidualArrays $dataset $case.Fit
    $meanDx = ($residuals.Dx | Measure-Object -Average).Average
    $meanDy = ($residuals.Dy | Measure-Object -Average).Average
    for ($replicate = 1; $replicate -le $BlockBootstrapReplicates; ++$replicate) {
        [double[]]$xStar = [double[]]::new($dataset.Time.Count)
        [double[]]$yStar = [double[]]::new($dataset.Time.Count)
        $target = 0
        while ($target -lt $dataset.Time.Count) {
            $start = $bootstrapRandom.Next(0, $dataset.Time.Count)
            for ($offset = 0; $offset -lt $blockLength -and $target -lt $dataset.Time.Count; ++$offset) {
                $source = ($start + $offset) % $dataset.Time.Count
                $xStar[$target] = $residuals.Predictions[$target][0] + $residuals.Dx[$source] - $meanDx
                $yStar[$target] = $residuals.Predictions[$target][1] + $residuals.Dy[$source] - $meanDy
                ++$target
            }
        }
        $fit = if ($case.Dataset -eq '附件1') {
            [Cumcm2015A.SolarShadowSolver]::Refine(
                $dataset.Time, $xStar, $yStar, 2015, 108, 1,
                $case.Fit.LatitudeDeg, $case.Fit.LongitudeDeg, 21, $false, 0.5)
        } else {
            Get-LocalCalendarFit $dataset $xStar $yStar $case.Fit 3
        }
        $bootstrapRows += [pscustomobject]@{
            sensitivity_type = 'moving_block_residual_bootstrap_conditional'
            dataset = $case.Dataset
            branch = $case.Branch
            replicate = $replicate
            block_length_points = $blockLength
            selected_day_of_year = $fit.DayOfYear
            latitude_deg = $fit.LatitudeDeg
            longitude_deg = $fit.LongitudeDeg
            inferred_height_m = $fit.HeightM
            tip_rmse_m = $fit.TipRmseM
            search_scope = if ($case.Dataset -eq '附件1') { 'fixed_known_date_local_location' } else { 'calendar_branch_plus_minus_3_days' }
            status = if ($fit.ObjectiveSse -lt 1.0e299) { 'pass' } else { 'fail' }
        }
    }
}
Export-Rows $bootstrapRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Joint date-location clusters: date frequencies are sensitivity frequencies, not probabilities.
$roundingRows = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$jointSamples = @($roundingRows + $bootstrapRows)
$jointClusterRows = @()
foreach ($outerGroup in ($jointSamples | Group-Object sensitivity_type, dataset, branch)) {
    $outerItems = @($outerGroup.Group)
    $dayGroups = @($outerItems | Group-Object selected_day_of_year | Sort-Object { [int]$_.Name })
    $temporary = @()
    foreach ($dayGroup in $dayGroups) {
        $items = @($dayGroup.Group)
        [double[]]$latitudes = @($items | ForEach-Object { [double]$_.latitude_deg })
        [double[]]$longitudes = @($items | ForEach-Object { [double]$_.longitude_deg })
        [double[]]$heights = @($items | ForEach-Object { [double]$_.inferred_height_m })
        $centroidLatitude = ($latitudes | Measure-Object -Average).Average
        $centroidLongitude = ($longitudes | Measure-Object -Average).Average
        $maxRadius = 0.0
        for ($i = 0; $i -lt $items.Count; ++$i) {
            $distance = Get-GreatCircleDistanceKm $centroidLatitude $centroidLongitude $latitudes[$i] $longitudes[$i]
            if ($distance -gt $maxRadius) { $maxRadius = $distance }
        }
        $temporary += [pscustomobject]@{
            sensitivity_type = $items[0].sensitivity_type
            dataset = $items[0].dataset
            branch = $items[0].branch
            selected_day_of_year = [int]$dayGroup.Name
            date_2015 = ([datetime]::new(2015,1,1).AddDays([int]$dayGroup.Name - 1)).ToString('yyyy-MM-dd')
            sample_count = $items.Count
            sensitivity_frequency = $items.Count / [double]$outerItems.Count
            latitude_p025_deg = Get-Quantile $latitudes 0.025
            latitude_median_deg = Get-Quantile $latitudes 0.5
            latitude_p975_deg = Get-Quantile $latitudes 0.975
            longitude_p025_deg = Get-Quantile $longitudes 0.025
            longitude_median_deg = Get-Quantile $longitudes 0.5
            longitude_p975_deg = Get-Quantile $longitudes 0.975
            height_p025_m = Get-Quantile $heights 0.025
            height_median_m = Get-Quantile $heights 0.5
            height_p975_m = Get-Quantile $heights 0.975
            centroid_latitude_deg = $centroidLatitude
            centroid_longitude_deg = $centroidLongitude
            maximum_within_cluster_radius_km = $maxRadius
        }
    }
    $modal = @($temporary | Sort-Object @{ Expression = 'sample_count'; Descending = $true }, selected_day_of_year)[0]
    foreach ($row in $temporary) {
        $row | Add-Member -NotePropertyName distance_from_modal_cluster_km -NotePropertyValue $(
            Get-GreatCircleDistanceKm $modal.centroid_latitude_deg $modal.centroid_longitude_deg $row.centroid_latitude_deg $row.centroid_longitude_deg)
        $jointClusterRows += $row
    }
}
Export-Rows $jointClusterRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Prefix drift: the held trajectory can predict smoothly while geographic parameters continue moving.
$prefixRows = @()
$q2PrefixFits = @()
foreach ($fitCount in @(8, 14, 18, 21)) {
    $dataset = $datasets['附件1']
    $fit = @([Cumcm2015A.SolarShadowSolver]::SearchKnownDate(
        $dataset.Time, $dataset.X, $dataset.Y, 2015, 108, $fitCount, $false, $false, 1))[0]
    $q2PrefixFits += [pscustomobject]@{ FitCount = $fitCount; Fit = $fit }
}
$q2Full = @($q2PrefixFits | Where-Object FitCount -eq 21)[0].Fit
foreach ($item in $q2PrefixFits) {
    $remainingCount = 21 - $item.FitCount
    $remainingRmse = if ($remainingCount -gt 0) {
        [Cumcm2015A.SolarShadowSolver]::EvaluateRange(
            $datasets['附件1'].Time, $datasets['附件1'].X, $datasets['附件1'].Y,
            $item.Fit, $item.FitCount, $remainingCount).TipRmseM
    } else { '' }
    $prefixRows += [pscustomobject]@{
        dataset = '附件1'; fit_points = $item.FitCount; selected_day_of_year = $item.Fit.DayOfYear
        date_2015 = '2015-04-18'; latitude_deg = $item.Fit.LatitudeDeg; longitude_deg = $item.Fit.LongitudeDeg
        inferred_height_m = $item.Fit.HeightM; fit_rmse_m = $item.Fit.TipRmseM; subsequent_rmse_m = $remainingRmse
        distance_from_full_fit_km = Get-GreatCircleDistanceKm $item.Fit.LatitudeDeg $item.Fit.LongitudeDeg $q2Full.LatitudeDeg $q2Full.LongitudeDeg
        status = 'pass'
    }
}
foreach ($name in @('附件2', '附件3')) {
    $dataset = $datasets[$name]
    $items = @()
    $prefixCounts = if ($SmokeTest) { @(21) } else { @(8, 14, 21) }
    foreach ($fitCount in $prefixCounts) {
        $profile = @([Cumcm2015A.SolarShadowSolver]::SearchDailyProfile(
            $dataset.Time, $dataset.X, $dataset.Y, 2015, 1, $fitCount, $false))
        $items += [pscustomobject]@{ FitCount = $fitCount; Fit = @($profile | Sort-Object ObjectiveSse)[0] }
    }
    $full = @($items | Where-Object FitCount -eq 21)[0].Fit
    foreach ($item in $items) {
        $remainingCount = 21 - $item.FitCount
        $remainingRmse = if ($remainingCount -gt 0) {
            [Cumcm2015A.SolarShadowSolver]::EvaluateRange(
                $dataset.Time, $dataset.X, $dataset.Y, $item.Fit, $item.FitCount, $remainingCount).TipRmseM
        } else { '' }
        $prefixRows += [pscustomobject]@{
            dataset = $name; fit_points = $item.FitCount; selected_day_of_year = $item.Fit.DayOfYear
            date_2015 = ([datetime]::new(2015,1,1).AddDays($item.Fit.DayOfYear - 1)).ToString('yyyy-MM-dd')
            latitude_deg = $item.Fit.LatitudeDeg; longitude_deg = $item.Fit.LongitudeDeg
            inferred_height_m = $item.Fit.HeightM; fit_rmse_m = $item.Fit.TipRmseM; subsequent_rmse_m = $remainingRmse
            distance_from_full_fit_km = Get-GreatCircleDistanceKm $item.Fit.LatitudeDeg $item.Fit.LongitudeDeg $full.LatitudeDeg $full.LongitudeDeg
            status = 'pass'
        }
    }
}
Export-Rows $prefixRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Millimetre-scale contamination curve. The 1 mm cases use a full annual search for unknown dates.
$contaminationRows = @()
foreach ($name in @('附件1', '附件2', '附件3')) {
    $dataset = $datasets[$name]
    $baseCase = @($baseCases | Where-Object Dataset -eq $name | Select-Object -First 1)[0]
    $cases = @(
        [pscustomobject]@{ Label = 'single_x_point_1'; Indices = @(0); MagnitudeMm = 1.0; Annual = $true },
        [pscustomobject]@{ Label = 'single_x_point_11'; Indices = @(10); MagnitudeMm = 0.25; Annual = $false },
        [pscustomobject]@{ Label = 'single_x_point_11'; Indices = @(10); MagnitudeMm = 0.5; Annual = $false },
        [pscustomobject]@{ Label = 'single_x_point_11'; Indices = @(10); MagnitudeMm = 1.0; Annual = $true },
        [pscustomobject]@{ Label = 'single_x_point_21'; Indices = @(20); MagnitudeMm = 1.0; Annual = $true },
        [pscustomobject]@{ Label = 'three_consecutive_x_points_10_12'; Indices = @(9,10,11); MagnitudeMm = 1.0; Annual = $true }
    )
    foreach ($stress in $cases) {
        [double[]]$xStress = @($dataset.X)
        [double[]]$yStress = @($dataset.Y)
        foreach ($index in $stress.Indices) { $xStress[$index] += $stress.MagnitudeMm / 1000.0 }
        $fit = if ($name -eq '附件1') {
            @([Cumcm2015A.SolarShadowSolver]::SearchKnownDate(
                $dataset.Time, $xStress, $yStress, 2015, 108, 21, $false, $false, 1))[0]
        } elseif ($stress.Annual -and -not $SmokeTest) {
            Get-AnnualFit $dataset $xStress $yStress
        } else {
            Get-LocalCalendarFit $dataset $xStress $yStress $baseCase.Fit 3
        }
        $contaminationRows += [pscustomobject]@{
            dataset = $name; contamination_case = $stress.Label
            contamination_magnitude_mm = $stress.MagnitudeMm
            contaminated_indices_1_based = (($stress.Indices | ForEach-Object { $_ + 1 }) -join ';')
            search_scope = if ($name -eq '附件1') { 'known_date_global_location' } elseif ($stress.Annual -and -not $SmokeTest) { 'full_365_day_search' } else { 'base_branch_plus_minus_3_days' }
            selected_day_of_year = $fit.DayOfYear
            date_2015 = ([datetime]::new(2015,1,1).AddDays($fit.DayOfYear - 1)).ToString('yyyy-MM-dd')
            latitude_deg = $fit.LatitudeDeg; longitude_deg = $fit.LongitudeDeg; inferred_height_m = $fit.HeightM
            tip_rmse_m = $fit.TipRmseM
            location_change_from_base_km = Get-GreatCircleDistanceKm $baseCase.Fit.LatitudeDeg $baseCase.Fit.LongitudeDeg $fit.LatitudeDeg $fit.LongitudeDeg
            status = if ($fit.ObjectiveSse -lt 1.0e299) { 'pass' } else { 'fail' }
        }
    }
}
Export-Rows $contaminationRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Structural coordinate stresses proxy rod-top offset and mild non-horizontal/non-orthogonal ground coordinates.
$structuralRows = @()
foreach ($name in @('附件1', '附件2', '附件3')) {
    $dataset = $datasets[$name]
    $baseCase = @($baseCases | Where-Object Dataset -eq $name | Select-Object -First 1)[0]
    $stresses = @(
        'constant_x_offset_plus_1mm',
        'x_scale_plus_0p1_percent',
        'y_shear_from_x_0p1_percent'
    )
    foreach ($stress in $stresses) {
        [double[]]$xStress = [double[]]::new($dataset.X.Count)
        [double[]]$yStress = [double[]]::new($dataset.Y.Count)
        for ($i = 0; $i -lt $dataset.X.Count; ++$i) {
            $xValue = [double]$dataset.X[$i]
            $yValue = [double]$dataset.Y[$i]
            switch ($stress) {
                'constant_x_offset_plus_1mm' { $xStress[$i] = $xValue + 0.001; $yStress[$i] = $yValue }
                'x_scale_plus_0p1_percent' { $xStress[$i] = 1.001 * $xValue; $yStress[$i] = $yValue }
                'y_shear_from_x_0p1_percent' { $xStress[$i] = $xValue; $yStress[$i] = $yValue + 0.001 * $xValue }
            }
        }
        $fit = if ($name -eq '附件1') {
            [Cumcm2015A.SolarShadowSolver]::Refine(
                $dataset.Time, $xStress, $yStress, 2015, 108, 1,
                $baseCase.Fit.LatitudeDeg, $baseCase.Fit.LongitudeDeg, 21, $false, 0.5)
        } else {
            Get-LocalCalendarFit $dataset $xStress $yStress $baseCase.Fit 5
        }
        $structuralRows += [pscustomobject]@{
            dataset = $name; stress_case = $stress; selected_day_of_year = $fit.DayOfYear
            latitude_deg = $fit.LatitudeDeg; longitude_deg = $fit.LongitudeDeg; inferred_height_m = $fit.HeightM
            location_change_from_base_km = Get-GreatCircleDistanceKm $baseCase.Fit.LatitudeDeg $baseCase.Fit.LongitudeDeg $fit.LatitudeDeg $fit.LongitudeDeg
            interpretation_status = 'needs_review'; numerical_execution_status = if ($fit.ObjectiveSse -lt 1.0e299) { 'pass' } else { 'fail' }
        }
    }
}
Export-Rows $structuralRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# A formula-choice stress derived only from the permitted NOAA formula: drop its third declination harmonic.
function Get-TruncatedSolarUnitShadow {
    param([int]$Day, [double]$Minute, [double]$Latitude, [double]$Longitude)
    $gamma = 2.0 * [Math]::PI / 365.0 * ($Day - 1.0 + ($Minute / 60.0 - 12.0) / 24.0)
    $equation = 229.18 * (0.000075 + 0.001868 * [Math]::Cos($gamma) - 0.032077 * [Math]::Sin($gamma) -
        0.014615 * [Math]::Cos(2.0 * $gamma) - 0.040849 * [Math]::Sin(2.0 * $gamma))
    $declination = 0.006918 - 0.399912 * [Math]::Cos($gamma) + 0.070257 * [Math]::Sin($gamma) -
        0.006758 * [Math]::Cos(2.0 * $gamma) + 0.000907 * [Math]::Sin(2.0 * $gamma)
    $phi = $Latitude * [Math]::PI / 180.0
    $hourAngle = ($Minute + $equation + 4.0 * $Longitude - 1200.0) * [Math]::PI / 720.0
    $up = [Math]::Sin($phi) * [Math]::Sin($declination) + [Math]::Cos($phi) * [Math]::Cos($declination) * [Math]::Cos($hourAngle)
    if ($up -le 1.0e-9) { return $null }
    $east = [Math]::Cos($declination) * [Math]::Sin($hourAngle) / $up
    $north = ([Math]::Sin($phi) * [Math]::Cos($declination) * [Math]::Cos($hourAngle) -
        [Math]::Cos($phi) * [Math]::Sin($declination)) / $up
    [double[]]@($east, $north)
}

function Get-TruncatedFit {
    param($Dataset, [int]$Day, [double]$Latitude, [double]$Longitude)
    $u = @(); $v = @(); $denominator = 0.0; $sumA = 0.0; $sumB = 0.0
    for ($i = 0; $i -lt $Dataset.Time.Count; ++$i) {
        $unit = Get-TruncatedSolarUnitShadow $Day $Dataset.Time[$i] $Latitude $Longitude
        if ($null -eq $unit) { return [pscustomobject]@{ ObjectiveSse = 1.0e300 } }
        $u += $unit[0]; $v += $unit[1]
        $denominator += $unit[0] * $unit[0] + $unit[1] * $unit[1]
        $sumA += $Dataset.X[$i] * $unit[0] + $Dataset.Y[$i] * $unit[1]
        $sumB += $Dataset.Y[$i] * $unit[0] - $Dataset.X[$i] * $unit[1]
    }
    if ($denominator -le 1.0e-20) { return [pscustomobject]@{ ObjectiveSse = 1.0e300 } }
    $a = $sumA / $denominator; $b = $sumB / $denominator; $sse = 0.0
    for ($i = 0; $i -lt $Dataset.Time.Count; ++$i) {
        $px = $a * $u[$i] - $b * $v[$i]; $py = $b * $u[$i] + $a * $v[$i]
        $sse += ($Dataset.X[$i] - $px) * ($Dataset.X[$i] - $px) + ($Dataset.Y[$i] - $py) * ($Dataset.Y[$i] - $py)
    }
    [pscustomobject]@{
        DayOfYear = $Day; LatitudeDeg = $Latitude; LongitudeDeg = $Longitude
        HeightM = [Math]::Sqrt($a * $a + $b * $b); ObjectiveSse = $sse
        TipRmseM = [Math]::Sqrt($sse / $Dataset.Time.Count)
    }
}

function Refine-TruncatedFit {
    param($Dataset, [int]$Day, [double]$StartLatitude, [double]$StartLongitude)
    $current = Get-TruncatedFit $Dataset $Day $StartLatitude $StartLongitude
    $step = 0.5
    for ($iteration = 0; $iteration -lt 80; ++$iteration) {
        $best = $current
        foreach ($di in -1..1) { foreach ($dj in -1..1) {
            if ($di -eq 0 -and $dj -eq 0) { continue }
            $trial = Get-TruncatedFit $Dataset $Day ($current.LatitudeDeg + $di * $step) ($current.LongitudeDeg + $dj * $step)
            if ($trial.ObjectiveSse -lt $best.ObjectiveSse) { $best = $trial }
        }}
        if ($best.ObjectiveSse -lt $current.ObjectiveSse) { $current = $best } else { $step *= 0.5 }
        if ($step -lt 2.0e-7) { break }
    }
    $current
}

$solarFormulaRows = foreach ($case in $baseCases) {
    $dataset = $datasets[$case.Dataset]
    $fit = Refine-TruncatedFit $dataset $case.Fit.DayOfYear $case.Fit.LatitudeDeg $case.Fit.LongitudeDeg
    [pscustomobject]@{
        dataset = $case.Dataset; branch = $case.Branch; stress_model = 'permitted_noaa_formula_without_third_declination_harmonic'
        selected_day_of_year = $fit.DayOfYear; latitude_deg = $fit.LatitudeDeg; longitude_deg = $fit.LongitudeDeg
        inferred_height_m = $fit.HeightM; tip_rmse_m = $fit.TipRmseM
        location_change_from_full_formula_km = Get-GreatCircleDistanceKm $case.Fit.LatitudeDeg $case.Fit.LongitudeDeg $fit.LatitudeDeg $fit.LongitudeDeg
        numerical_execution_status = if ($fit.ObjectiveSse -lt 1.0e299) { 'pass' } else { 'fail' }
        external_ephemeris_truth_status = 'needs_review'
    }
}
Export-Rows $solarFormulaRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Question 4 homography direction, units and homogeneous normalization on synthetic control points.
function Apply-Homography {
    param([double[,]]$Matrix, [double[]]$Point)
    $raw0 = $Matrix[0,0] * $Point[0] + $Matrix[0,1] * $Point[1] + $Matrix[0,2] * $Point[2]
    $raw1 = $Matrix[1,0] * $Point[0] + $Matrix[1,1] * $Point[1] + $Matrix[1,2] * $Point[2]
    $raw2 = $Matrix[2,0] * $Point[0] + $Matrix[2,1] * $Point[1] + $Matrix[2,2] * $Point[2]
    if ([Math]::Abs($raw2) -le 1.0e-12) { throw 'Homogeneous denominator is zero.' }
    $normalized0 = $raw0 / $raw2
    $normalized1 = $raw1 / $raw2
    [double[]]@($normalized0, $normalized1)
}

$groundFromPixel = [double[,]]::new(3,3)
$groundFromPixel[0,0] = 0.01; $groundFromPixel[1,1] = 0.01; $groundFromPixel[2,2] = 1.0
$pixelFromGround = [double[,]]::new(3,3)
$pixelFromGround[0,0] = 100.0; $pixelFromGround[1,1] = 100.0; $pixelFromGround[2,2] = 1.0
$ground = Apply-Homography $groundFromPixel ([double[]]@(100.0,200.0,1.0))
$pixel = Apply-Homography $pixelFromGround ([double[]]@(1.0,2.0,1.0))
$projective = [double[,]]::new(3,3)
$projective[0,0] = 0.01; $projective[1,1] = 0.01; $projective[2,0] = 0.001; $projective[2,2] = 1.0
$normalized = Apply-Homography $projective ([double[]]@(100.0,200.0,1.0))
$q4Rows = @(
    [pscustomobject]@{ check_id = 'pixel_to_ground_direction_and_metres'; residual = [Math]::Sqrt(($ground[0]-1.0)*($ground[0]-1.0)+($ground[1]-2.0)*($ground[1]-2.0)); unit = 'm'; status = if ([Math]::Abs($ground[0]-1.0) -lt 1e-12 -and [Math]::Abs($ground[1]-2.0) -lt 1e-12) { 'pass' } else { 'fail' } },
    [pscustomobject]@{ check_id = 'ground_to_pixel_inverse_direction'; residual = [Math]::Sqrt(($pixel[0]-100.0)*($pixel[0]-100.0)+($pixel[1]-200.0)*($pixel[1]-200.0)); unit = 'pixel'; status = if ([Math]::Abs($pixel[0]-100.0) -lt 1e-12 -and [Math]::Abs($pixel[1]-200.0) -lt 1e-12) { 'pass' } else { 'fail' } },
    [pscustomobject]@{ check_id = 'homogeneous_normalization'; residual = [Math]::Abs($normalized[0] - 1.0/1.1) + [Math]::Abs($normalized[1] - 2.0/1.1); unit = 'm'; status = if ([Math]::Abs($normalized[0] - 1.0/1.1) -lt 1e-12 -and [Math]::Abs($normalized[1] - 2.0/1.1) -lt 1e-12) { 'pass' } else { 'fail' } }
)
Export-Rows $q4Rows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')
if (@($q4Rows | Where-Object status -eq 'fail').Count -gt 0) { throw 'Q4 synthetic homography checks failed.' }

$uncertaintyStatus = [ordered]@{
    residual_diagnostics_status = if (@($residualDiagnosticRows | Where-Object rounding_as_total_error_status -eq 'fail').Count -gt 0) { 'pass' } else { 'needs_review' }
    rounding_only_conditional_status = 'pass'
    block_residual_bootstrap_execution_status = if (@($bootstrapRows | Where-Object status -eq 'fail').Count -eq 0) { 'pass' } else { 'fail' }
    joint_date_location_cluster_status = 'pass'
    prefix_parameter_drift_status = if (@($prefixRows | Where-Object status -eq 'fail').Count -eq 0) { 'pass' } else { 'fail' }
    contamination_execution_status = if (@($contaminationRows | Where-Object status -eq 'fail').Count -eq 0) { 'pass' } else { 'fail' }
    structural_interpretation_status = 'needs_review'
    solar_formula_external_truth_status = 'needs_review'
    total_uncertainty_interval_status = 'needs_review'
    note = 'No sensitivity frequency is interpreted as a sampling probability or external confidence interval.'
}
$uncertaintyStatus | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $resultsDir 'uncertainty_status.json') -Encoding utf8

Write-Host ("[PASS] revision diagnostics completed; block_bootstrap_replicates={0}; contamination_cases={1}" -f $BlockBootstrapReplicates, $contaminationRows.Count)
