param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Invariant = [System.Globalization.CultureInfo]::InvariantCulture
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$Workspace = [System.IO.Path]::GetFullPath($Workspace)
$ResultsDir = Join-Path $Workspace 'results'
$FiguresDir = Join-Path $Workspace 'figures'
$InputDir = Join-Path $Workspace 'input'
$ConfigPath = Join-Path $PSScriptRoot 'model-parameters.json'
[System.IO.Directory]::CreateDirectory($ResultsDir) | Out-Null
[System.IO.Directory]::CreateDirectory($FiguresDir) | Out-Null

function Write-TextFile {
    param([string]$Path, [string]$Text)
    $parent = [System.IO.Path]::GetDirectoryName($Path)
    if ($parent) { [System.IO.Directory]::CreateDirectory($parent) | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Text, $Utf8NoBom)
}

function Write-JsonFile {
    param([string]$Path, $Value)
    $json = $Value | ConvertTo-Json -Depth 20
    Write-TextFile -Path $Path -Text ($json + "`n")
}

function Write-CsvFile {
    param([string]$Path, [object[]]$Rows)
    $lines = @($Rows | ConvertTo-Csv -NoTypeInformation)
    Write-TextFile -Path $Path -Text (($lines -join "`n") + "`n")
}

function Round-Number {
    param([double]$Value, [int]$Digits = 6)
    return [Math]::Round($Value, $Digits, [MidpointRounding]::AwayFromZero)
}

function Get-Median {
    param([double[]]$Values)
    $s = @($Values | Sort-Object)
    if ($s.Count -eq 0) { return $null }
    $mid = [int][Math]::Floor($s.Count / 2)
    if ($s.Count % 2 -eq 1) { return [double]$s[$mid] }
    return ([double]$s[$mid - 1] + [double]$s[$mid]) / 2.0
}

function Get-KinematicResult {
    param(
        [double]$Inflow,
        [double]$Capacity,
        [double]$DistanceKm,
        [int]$Lanes,
        [double]$FreeSpeed,
        [double]$WaveSpeed,
        [double]$JamDensityPerLane
    )
    if ($Inflow -lt 0 -or $Capacity -lt 0 -or $DistanceKm -lt 0 -or $Lanes -le 0 -or
        $FreeSpeed -le 0 -or $WaveSpeed -le 0 -or $JamDensityPerLane -le 0) {
        return [pscustomobject]@{ status = 'fail'; reason = 'nonpositive_or_invalid_parameter' }
    }
    $totalJam = $Lanes * $JamDensityPerLane
    $upstreamCapacity = $Lanes * ($FreeSpeed * $WaveSpeed / ($FreeSpeed + $WaveSpeed)) * $JamDensityPerLane
    $arrivalDensity = $Inflow / $FreeSpeed
    $congestedDensity = $totalJam - $Capacity / $WaveSpeed
    $densityJump = $congestedDensity - $arrivalDensity
    if ($densityJump -le 0 -or $Inflow -gt $upstreamCapacity) {
        return [pscustomobject]@{
            status = 'fail'; reason = 'infeasible_fundamental_diagram_state'
            arrival_density_pcu_km = Round-Number $arrivalDensity
            congested_density_pcu_km = Round-Number $congestedDensity
            upstream_capacity_pcu_h = Round-Number $upstreamCapacity
        }
    }
    if ($DistanceKm -eq 0) {
        return [pscustomobject]@{
            status = 'pass'; regime = 'already_at_target'; queue_grows = ($Inflow -gt $Capacity)
            arrival_density_pcu_km = Round-Number $arrivalDensity
            congested_density_pcu_km = Round-Number $congestedDensity
            density_jump_pcu_km = Round-Number $densityJump
            queue_growth_speed_km_h = if ($Inflow -gt $Capacity) { Round-Number (($Inflow - $Capacity) / $densityJump) } else { 0.0 }
            time_to_distance_min = 0.0; point_queue_time_min = 0.0
            upstream_capacity_pcu_h = Round-Number $upstreamCapacity
        }
    }
    if ($Inflow -le $Capacity) {
        return [pscustomobject]@{
            status = 'pass'; regime = 'no_growing_queue'; queue_grows = $false
            arrival_density_pcu_km = Round-Number $arrivalDensity
            congested_density_pcu_km = Round-Number $congestedDensity
            density_jump_pcu_km = Round-Number $densityJump
            queue_growth_speed_km_h = 0.0; time_to_distance_min = $null
            point_queue_time_min = $null
            upstream_capacity_pcu_h = Round-Number $upstreamCapacity
        }
    }
    $growthSpeed = ($Inflow - $Capacity) / $densityJump
    $timeMinutes = 60.0 * $DistanceKm / $growthSpeed
    $pointQueueMinutes = 60.0 * $DistanceKm * $totalJam / ($Inflow - $Capacity)
    return [pscustomobject]@{
        status = 'pass'; regime = 'growing_queue'; queue_grows = $true
        arrival_density_pcu_km = Round-Number $arrivalDensity
        congested_density_pcu_km = Round-Number $congestedDensity
        density_jump_pcu_km = Round-Number $densityJump
        queue_growth_speed_km_h = Round-Number $growthSpeed
        time_to_distance_min = Round-Number $timeMinutes
        point_queue_time_min = Round-Number $pointQueueMinutes
        upstream_capacity_pcu_h = Round-Number $upstreamCapacity
    }
}

function Get-QueueLengthM {
    param([double]$Minutes, $KinematicResult)
    if (-not $KinematicResult.queue_grows) { return 0.0 }
    return Round-Number (1000.0 * $KinematicResult.queue_growth_speed_km_h * $Minutes / 60.0)
}

$Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$Seed = [int]$Config.seed
$null = Get-Random -SetSeed $Seed

$sourceRelative = @(
    'input\problem\<SOURCE_FILE_REDACTED>',
    'input\attachments\<SOURCE_FILE_REDACTED>'
)
$sourceFiles = @()
foreach ($relative in $sourceRelative) {
    $absolute = Join-Path $Workspace $relative
    if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) {
        throw "Missing allowed input: $relative"
    }
    $item = Get-Item -LiteralPath $absolute
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $absolute
    $sourceFiles += [pscustomobject]@{
        path = $relative.Replace('\', '/')
        bytes = [long]$item.Length
        sha256 = $hash.Hash.ToLowerInvariant()
        status = 'pass'
    }
}
$videoExtensions = @('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.m4v')
$videoFiles = @(
    Get-ChildItem -LiteralPath $InputDir -Recurse -File |
        Where-Object { $videoExtensions -contains $_.Extension.ToLowerInvariant() } |
        ForEach-Object { $_.FullName.Substring($Workspace.Length + 1).Replace('\', '/') }
)
$inputManifest = [ordered]@{
    schema_version = 1
    source_files = $sourceFiles
    video_files = $videoFiles
    video_files_found = $videoFiles.Count
    input_completeness_status = if ($videoFiles.Count -ge 2) { 'pass' } else { 'needs_review' }
    note = 'The task references two videos; no network retrieval is performed.'
}
Write-JsonFile (Join-Path $ResultsDir 'input_manifest.json') $inputManifest

$g = $Config.given
$e = $Config.engineering_scenario
$lanes = [int]$g.lanes
$distanceKm = [double]$g.q4_distance_m / 1000.0
$inflow = [double]$g.q4_inflow_pcu_h
$freeSpeed = [double]$e.free_flow_speed_km_h
$waveSpeed = [double]$e.backward_wave_speed_km_h
$jamDensity = [double]$e.jam_density_pcu_km_lane
$centralCapacity = [double]$e.incident_capacity_pcu_h

$central = Get-KinematicResult -Inflow $inflow -Capacity $centralCapacity -DistanceKm $distanceKm `
    -Lanes $lanes -FreeSpeed $freeSpeed -WaveSpeed $waveSpeed -JamDensityPerLane $jamDensity
if ($central.status -ne 'pass') { throw 'Central kinematic scenario is infeasible.' }

$capacityRows = @()
foreach ($capacity in $Config.capacity_grid_pcu_h) {
    $r = Get-KinematicResult -Inflow $inflow -Capacity ([double]$capacity) -DistanceKm $distanceKm `
        -Lanes $lanes -FreeSpeed $freeSpeed -WaveSpeed $waveSpeed -JamDensityPerLane $jamDensity
    $capacityRows += [pscustomobject]@{
        capacity_pcu_h = [double]$capacity
        regime = $r.regime
        queue_growth_speed_km_h = $r.queue_growth_speed_km_h
        time_to_140m_min = $r.time_to_distance_min
        point_queue_time_min = $r.point_queue_time_min
        calculation_status = $r.status
        evidence_status = 'needs_review'
    }
}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $capacityRows

$mainLow = Get-KinematicResult -Inflow $inflow -Capacity ([double]$e.main_capacity_low_pcu_h) -DistanceKm $distanceKm `
    -Lanes $lanes -FreeSpeed $freeSpeed -WaveSpeed $waveSpeed -JamDensityPerLane $jamDensity
$mainHigh = Get-KinematicResult -Inflow $inflow -Capacity ([double]$e.main_capacity_high_pcu_h) -DistanceKm $distanceKm `
    -Lanes $lanes -FreeSpeed $freeSpeed -WaveSpeed $waveSpeed -JamDensityPerLane $jamDensity

$oneAtATime = @()
foreach ($capacity in @(800.0, 1000.0, 1200.0)) {
    $r = Get-KinematicResult $inflow $capacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity
    $oneAtATime += [pscustomobject]@{ parameter = 'capacity_pcu_h'; value = $capacity; time_to_140m_min = $r.time_to_distance_min }
}
foreach ($value in @(30.0, 40.0, 50.0)) {
    $r = Get-KinematicResult $inflow $centralCapacity $distanceKm $lanes $value $waveSpeed $jamDensity
    $oneAtATime += [pscustomobject]@{ parameter = 'free_flow_speed_km_h'; value = $value; time_to_140m_min = $r.time_to_distance_min }
}
foreach ($value in @(10.0, 15.0, 20.0)) {
    $r = Get-KinematicResult $inflow $centralCapacity $distanceKm $lanes $freeSpeed $value $jamDensity
    $oneAtATime += [pscustomobject]@{ parameter = 'backward_wave_speed_km_h'; value = $value; time_to_140m_min = $r.time_to_distance_min }
}
foreach ($value in @(120.0, 140.0, 160.0)) {
    $r = Get-KinematicResult $inflow $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $value
    $oneAtATime += [pscustomobject]@{ parameter = 'jam_density_pcu_km_lane'; value = $value; time_to_140m_min = $r.time_to_distance_min }
}
foreach ($value in @(120.0, 140.0, 160.0)) {
    $r = Get-KinematicResult $inflow $centralCapacity ($value / 1000.0) $lanes $freeSpeed $waveSpeed $jamDensity
    $oneAtATime += [pscustomobject]@{ parameter = 'distance_m'; value = $value; time_to_140m_min = $r.time_to_distance_min }
}
foreach ($value in @(1400.0, 1500.0, 1600.0)) {
    $r = Get-KinematicResult $value $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity
    $oneAtATime += [pscustomobject]@{ parameter = 'inflow_pcu_h'; value = $value; time_to_140m_min = $r.time_to_distance_min }
}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $oneAtATime

$jointRows = @()
foreach ($capacity in $Config.sensitivity_grid.capacity_pcu_h) {
    foreach ($vf in $Config.sensitivity_grid.free_flow_speed_km_h) {
        foreach ($w in $Config.sensitivity_grid.backward_wave_speed_km_h) {
            foreach ($kj in $Config.sensitivity_grid.jam_density_pcu_km_lane) {
                $r = Get-KinematicResult $inflow ([double]$capacity) $distanceKm $lanes ([double]$vf) ([double]$w) ([double]$kj)
                $jointRows += [pscustomobject]@{
                    capacity_pcu_h = [double]$capacity
                    free_flow_speed_km_h = [double]$vf
                    backward_wave_speed_km_h = [double]$w
                    jam_density_pcu_km_lane = [double]$kj
                    time_to_140m_min = $r.time_to_distance_min
                    status = $r.status
                }
            }
        }
    }
}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $jointRows
$jointTimes = [double[]]@($jointRows | ForEach-Object { [double]$_.time_to_140m_min })
$jointMin = ($jointTimes | Measure-Object -Minimum).Minimum
$jointMax = ($jointTimes | Measure-Object -Maximum).Maximum
$jointMedian = Get-Median $jointTimes

$timeRows = @()
$arrivalSeconds = 60.0 * [double]$central.time_to_distance_min
$lastGridSecond = [Math]::Ceiling($arrivalSeconds / 15.0) * 15.0
for ($seconds = 0.0; $seconds -le $lastGridSecond; $seconds += 15.0) {
    $minutes = $seconds / 60.0
    $lwrM = Get-QueueLengthM $minutes $central
    $excessPcu = [Math]::Max(0.0, ($inflow - $centralCapacity) * $seconds / 3600.0)
    $pointM = 1000.0 * $excessPcu / ($lanes * $jamDensity)
    $timeRows += [pscustomobject]@{
        time_s = Round-Number $seconds 3
        time_min = Round-Number $minutes
        lwr_queue_length_m = $lwrM
        point_queue_length_m = Round-Number $pointM
    }
}
$timeRows += [pscustomobject]@{
    time_s = Round-Number $arrivalSeconds
    time_min = $central.time_to_distance_min
    lwr_queue_length_m = [double]$g.q4_distance_m
    point_queue_length_m = Round-Number (1000.0 * (($inflow - $centralCapacity) * $arrivalSeconds / 3600.0) / ($lanes * $jamDensity))
}
$timeRows = @($timeRows | Sort-Object time_s -Unique)
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $timeRows

$q3Rows = @()
foreach ($qValue in @(1200.0, 1500.0, 1800.0)) {
    foreach ($capacity in @(600.0, 800.0, 1000.0, 1200.0, 1400.0)) {
        $r = Get-KinematicResult $qValue $capacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity
        foreach ($duration in @(2.0, 5.0, 10.0)) {
            $lengthM = if ($r.status -eq 'pass' -and $r.queue_grows) { Get-QueueLengthM $duration $r } else { 0.0 }
            $q3Rows += [pscustomobject]@{
                inflow_pcu_h = $qValue
                capacity_pcu_h = $capacity
                incident_duration_min = $duration
                queue_length_m = $lengthM
                regime = $r.regime
            }
        }
    }
}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $q3Rows

$laneRows = @()
$sortedTurns = @($Config.turn_shares | Sort-Object { 1.0 - [double]$_.share })
for ($i = 0; $i -lt $sortedTurns.Count; $i++) {
    $turn = $sortedTurns[$i]
    $laneRows += [pscustomobject]@{
        remaining_lane_role = [string]$turn.movement
        native_demand_share = Round-Number ([double]$turn.share) 3
        mandatory_merge_proxy = Round-Number (1.0 - [double]$turn.share) 3
        predicted_capacity_rank = $i + 1
        evidence_layer = 'engineering_inference'
        status = 'needs_review'
    }
}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $laneRows

$annotationRows = @(
    [pscustomobject]@{ video_id = 'video1'; interval_start_s = $null; interval_end_s = $null; passenger_car_count = $null; bus_or_truck_count = $null; electric_bicycle_count = $null; saturated_demand = $null; occupied_lanes = $null; pcu_flow_pcu_h = $null; status = 'needs_review' },
    [pscustomobject]@{ video_id = 'video2'; interval_start_s = $null; interval_end_s = $null; passenger_car_count = $null; bus_or_truck_count = $null; electric_bicycle_count = $null; saturated_demand = $null; occupied_lanes = $null; pcu_flow_pcu_h = $null; status = 'needs_review' }
)
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $annotationRows

$summary = [ordered]@{
    schema_version = 1
    overall_status = 'needs_review'
    seed = $Seed
    q1 = [ordered]@{
        status = 'needs_review'
        numeric_capacity_available = $false
        reason = 'video1 is absent from the allowed workspace'
        method = 'complete-cycle PCU counts with saturation screening and robust segmentation'
    }
    q2 = [ordered]@{
        status = 'needs_review'
        empirical_difference_available = $false
        reason = 'video1/video2 and their occupied-lane mapping are absent'
        conditional_capacity_order = @($laneRows | Sort-Object predicted_capacity_rank | ForEach-Object { $_.remaining_lane_role })
        merge_proxy = $laneRows
    }
    q3 = [ordered]@{
        status = 'pass'
        evidence_layer = 'model_internal'
        relation = 'dL/dt=(q-C)/(m*k_j-C/w-q/v_f), reflected at L=0'
        caveat = 'constant-state form; use piecewise integration for time-varying q or C'
    }
    q4 = [ordered]@{
        calculation_status = 'pass'
        case_specific_status = 'needs_review'
        distance_m = [double]$g.q4_distance_m
        inflow_pcu_h = $inflow
        assumed_capacity_pcu_h = $centralCapacity
        free_flow_speed_km_h = $freeSpeed
        backward_wave_speed_km_h = $waveSpeed
        jam_density_pcu_km_lane = $jamDensity
        arrival_density_pcu_km = $central.arrival_density_pcu_km
        congested_density_pcu_km = $central.congested_density_pcu_km
        queue_growth_speed_km_h = $central.queue_growth_speed_km_h
        central_time_min = $central.time_to_distance_min
        point_queue_baseline_time_min = $central.point_queue_time_min
        main_capacity_range_pcu_h = @([double]$e.main_capacity_low_pcu_h, [double]$e.main_capacity_high_pcu_h)
        time_over_main_capacity_range_min = @($mainLow.time_to_distance_min, $mainHigh.time_to_distance_min)
        joint_grid_time_range_min = @((Round-Number $jointMin), (Round-Number $jointMax))
        joint_grid_median_time_min = Round-Number $jointMedian
        no_growth_condition = 'capacity >= inflow'
        interpretation = 'The central value is an engineering scenario, not a video-derived estimate.'
    }
}
Write-JsonFile (Join-Path $ResultsDir 'summary.json') $summary
Write-JsonFile (Join-Path $ResultsDir 'model_parameters_used.json') $Config

$numericLengthKm = 0.0
$numericSeconds = 0.0
$stepSeconds = 1.0
while ($numericLengthKm + 1e-15 -lt $distanceKm -and $numericSeconds -lt 7200.0) {
    $numericLengthKm += [double]$central.queue_growth_speed_km_h * $stepSeconds / 3600.0
    $numericSeconds += $stepSeconds
}
$analyticSeconds = 60.0 * [double]$central.time_to_distance_min
$numericErrorSeconds = [Math]::Abs($numericSeconds - $analyticSeconds)
$growthRows = @($capacityRows | Where-Object { $_.regime -eq 'growing_queue' } | Sort-Object capacity_pcu_h)
$monotone = $true
for ($i = 1; $i -lt $growthRows.Count; $i++) {
    if ([double]$growthRows[$i].time_to_140m_min -le [double]$growthRows[$i - 1].time_to_140m_min) { $monotone = $false }
}
$zeroDistance = Get-KinematicResult $inflow $centralCapacity 0.0 $lanes $freeSpeed $waveSpeed $jamDensity
$noGrowth = Get-KinematicResult 1200.0 1500.0 $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity
$verificationChecks = @(
    [pscustomobject]@{ id = 'V01_source_identity'; status = if ($sourceFiles.Count -eq 2) { 'pass' } else { 'fail' }; metric = $sourceFiles.Count; tolerance = 2 },
    [pscustomobject]@{ id = 'V02_video_completeness'; status = if ($videoFiles.Count -ge 2) { 'pass' } else { 'needs_review' }; metric = $videoFiles.Count; tolerance = 2 },
    [pscustomobject]@{ id = 'V03_analytic_vs_1s_euler'; status = if ($numericErrorSeconds -le 1.000001) { 'pass' } else { 'fail' }; metric = Round-Number $numericErrorSeconds; tolerance = 1.000001 },
    [pscustomobject]@{ id = 'V04_capacity_monotonicity'; status = if ($monotone) { 'pass' } else { 'fail' }; metric = $monotone; tolerance = $true },
    [pscustomobject]@{ id = 'V05_zero_distance'; status = if ($zeroDistance.time_to_distance_min -eq 0.0) { 'pass' } else { 'fail' }; metric = $zeroDistance.time_to_distance_min; tolerance = 0.0 },
    [pscustomobject]@{ id = 'V06_no_growth_when_q_le_c'; status = if (-not $noGrowth.queue_grows -and $null -eq $noGrowth.time_to_distance_min) { 'pass' } else { 'fail' }; metric = $noGrowth.regime; tolerance = 'no_growing_queue' },
    [pscustomobject]@{ id = 'V07_density_order'; status = if ([double]$central.congested_density_pcu_km -gt [double]$central.arrival_density_pcu_km) { 'pass' } else { 'fail' }; metric = $central.density_jump_pcu_km; tolerance = '>0' },
    [pscustomobject]@{ id = 'V08_external_validity'; status = 'needs_review'; metric = 'no video observations'; tolerance = 'requires video1/video2' }
)
$hardFailures = @($verificationChecks | Where-Object { $_.status -eq 'fail' }).Count
$verification = [ordered]@{
    schema_version = 1
    overall_status = if ($hardFailures -gt 0) { 'fail' } elseif (@($verificationChecks | Where-Object { $_.status -eq 'needs_review' }).Count -gt 0) { 'needs_review' } else { 'pass' }
    analytic_time_s = Round-Number $analyticSeconds
    numerical_time_s = Round-Number $numericSeconds
    checks = $verificationChecks
    claim_limit = 'pass applies to implementation and model-internal invariants only'
}
Write-JsonFile (Join-Path $ResultsDir 'verification.json') $verification

$keyNumbers = @(
    [pscustomobject]@{ key = 'q4.central_time_min'; value = Round-Number $central.time_to_distance_min 2; unit = 'min'; evidence_layer = 'engineering_scenario'; status = 'needs_review' },
    [pscustomobject]@{ key = 'q4.point_queue_time_min'; value = Round-Number $central.point_queue_time_min 2; unit = 'min'; evidence_layer = 'engineering_scenario'; status = 'needs_review' },
    [pscustomobject]@{ key = 'q4.capacity_800_time_min'; value = Round-Number $mainLow.time_to_distance_min 2; unit = 'min'; evidence_layer = 'engineering_scenario'; status = 'needs_review' },
    [pscustomobject]@{ key = 'q4.capacity_1200_time_min'; value = Round-Number $mainHigh.time_to_distance_min 2; unit = 'min'; evidence_layer = 'engineering_scenario'; status = 'needs_review' },
    [pscustomobject]@{ key = 'q4.queue_speed_km_h'; value = Round-Number $central.queue_growth_speed_km_h 3; unit = 'km/h'; evidence_layer = 'engineering_scenario'; status = 'needs_review' },
    [pscustomobject]@{ key = 'q2.right_merge_proxy'; value = 0.79; unit = 'share'; evidence_layer = 'engineering_inference'; status = 'needs_review' },
    [pscustomobject]@{ key = 'q2.through_merge_proxy'; value = 0.56; unit = 'share'; evidence_layer = 'engineering_inference'; status = 'needs_review' },
    [pscustomobject]@{ key = 'q2.left_merge_proxy'; value = 0.65; unit = 'share'; evidence_layer = 'engineering_inference'; status = 'needs_review' }
)
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $keyNumbers

Add-Type -AssemblyName System.Drawing
function New-BaseChart {
    param([string]$Path, [string]$Title, [string]$Subtitle, [string]$XLabel, [string]$YLabel)
    $bitmap = [System.Drawing.Bitmap]::new(1100, 700)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::White)
    $font = [System.Drawing.Font]::new('Microsoft YaHei', 11)
    $titleFont = [System.Drawing.Font]::new('Microsoft YaHei', 20, [System.Drawing.FontStyle]::Bold)
    $smallFont = [System.Drawing.Font]::new('Microsoft YaHei', 9)
    $axisPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(60, 60, 60), 2)
    $gridPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(220, 225, 230), 1)
    $graphics.DrawString($Title, $titleFont, [System.Drawing.Brushes]::Black, 70, 18)
    $graphics.DrawString($Subtitle, $smallFont, [System.Drawing.Brushes]::DimGray, 72, 55)
    $graphics.DrawLine($axisPen, 90, 610, 1040, 610)
    $graphics.DrawLine($axisPen, 90, 90, 90, 610)
    $graphics.DrawString($XLabel, $font, [System.Drawing.Brushes]::Black, 500, 650)
    $graphics.TranslateTransform(25, 410)
    $graphics.RotateTransform(-90)
    $graphics.DrawString($YLabel, $font, [System.Drawing.Brushes]::Black, 0, 0)
    $graphics.ResetTransform()
    return [pscustomobject]@{ Bitmap=$bitmap; Graphics=$graphics; Font=$font; SmallFont=$smallFont; TitleFont=$titleFont; AxisPen=$axisPen; GridPen=$gridPen; Path=$Path }
}

function Close-Chart {
    param($Chart)
    $Chart.Bitmap.Save($Chart.Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $Chart.Graphics.Dispose(); $Chart.Bitmap.Dispose(); $Chart.Font.Dispose(); $Chart.SmallFont.Dispose();
    $Chart.TitleFont.Dispose(); $Chart.AxisPen.Dispose(); $Chart.GridPen.Dispose()
}

function Map-X([double]$x, [double]$xmin, [double]$xmax) { return [single](90.0 + 950.0 * ($x - $xmin) / ($xmax - $xmin)) }
function Map-Y([double]$y, [double]$ymin, [double]$ymax) { return [single](610.0 - 520.0 * ($y - $ymin) / ($ymax - $ymin)) }

$queueChart = New-BaseChart (Join-Path $FiguresDir '<SOURCE_FILE_REDACTED>') '问题4：排队增长' '工程情景 C=1000 pcu/h；不是视频观测' '时间 / min' '排队长度 / m'
$gc = $queueChart.Graphics
$xmax = 1.15 * [double]$central.point_queue_time_min
$ymax = 225.0
for ($i=0; $i -le 5; $i++) { $y=45.0*$i; $py=Map-Y $y 0 $ymax; $gc.DrawLine($queueChart.GridPen,90,$py,1040,$py); $gc.DrawString($y.ToString('0',$Invariant),$queueChart.SmallFont,[System.Drawing.Brushes]::DimGray,45,$py-7) }
for ($i=0; $i -le 8; $i++) { $x=$xmax*$i/8.0; $px=Map-X $x 0 $xmax; $gc.DrawLine($queueChart.GridPen,$px,90,$px,610); $gc.DrawString($x.ToString('0.0',$Invariant),$queueChart.SmallFont,[System.Drawing.Brushes]::DimGray,$px-12,615) }
$lwrPoints=[System.Collections.Generic.List[System.Drawing.PointF]]::new(); $pointPoints=[System.Collections.Generic.List[System.Drawing.PointF]]::new()
for($i=0;$i -le 100;$i++){ $t=$xmax*$i/100.0; $lwr=[Math]::Min($ymax,1000.0*[double]$central.queue_growth_speed_km_h*$t/60.0); $point=[Math]::Min($ymax,1000.0*(($inflow-$centralCapacity)*$t/60.0)/($lanes*$jamDensity)); $lwrPoints.Add([System.Drawing.PointF]::new((Map-X $t 0 $xmax),(Map-Y $lwr 0 $ymax))); $pointPoints.Add([System.Drawing.PointF]::new((Map-X $t 0 $xmax),(Map-Y $point 0 $ymax))) }
$bluePen=[System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(31,119,180),4); $orangePen=[System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(255,127,14),4); $targetPen=[System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(180,40,40),2); $targetPen.DashStyle=[System.Drawing.Drawing2D.DashStyle]::Dash
$gc.DrawLines($bluePen,$lwrPoints.ToArray()); $gc.DrawLines($orangePen,$pointPoints.ToArray()); $targetY=Map-Y 140 0 $ymax; $gc.DrawLine($targetPen,90,$targetY,1040,$targetY)
$gc.DrawString('运动波模型',$queueChart.Font,[System.Drawing.Brushes]::SteelBlue,760,115); $gc.DrawString('点队列基线',$queueChart.Font,[System.Drawing.Brushes]::DarkOrange,760,140); $gc.DrawString('140 m',$queueChart.Font,[System.Drawing.Brushes]::Firebrick,950,$targetY-25)
$bluePen.Dispose();$orangePen.Dispose();$targetPen.Dispose(); Close-Chart $queueChart

$capPlotRows=@($capacityRows | Where-Object { $_.regime -eq 'growing_queue' -and [double]$_.capacity_pcu_h -le 1400 })
$capYMax=1.1*(($capPlotRows | Measure-Object -Property time_to_140m_min -Maximum).Maximum)
$capChart=New-BaseChart (Join-Path $FiguresDir '<SOURCE_FILE_REDACTED>') '问题4：容量敏感性' '其余参数固定；C 接近 1500 pcu/h 时到达时间发散' '事故断面能力 C / (pcu/h)' '到达 140 m 时间 / min'
$gc=$capChart.Graphics
for($i=0;$i -le 5;$i++){ $y=$capYMax*$i/5.0; $py=Map-Y $y 0 $capYMax; $gc.DrawLine($capChart.GridPen,90,$py,1040,$py);$gc.DrawString($y.ToString('0.0',$Invariant),$capChart.SmallFont,[System.Drawing.Brushes]::DimGray,43,$py-7)}
for($i=0;$i -le 8;$i++){ $x=600+100*$i; $px=Map-X $x 600 1400; $gc.DrawLine($capChart.GridPen,$px,90,$px,610);$gc.DrawString($x.ToString('0',$Invariant),$capChart.SmallFont,[System.Drawing.Brushes]::DimGray,$px-16,615)}
$capPoints=[System.Collections.Generic.List[System.Drawing.PointF]]::new(); foreach($row in $capPlotRows){$capPoints.Add([System.Drawing.PointF]::new((Map-X ([double]$row.capacity_pcu_h) 600 1400),(Map-Y ([double]$row.time_to_140m_min) 0 $capYMax)))}
$purplePen=[System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(120,70,170),4);$gc.DrawLines($purplePen,$capPoints.ToArray());foreach($pt in $capPoints){$gc.FillEllipse([System.Drawing.Brushes]::Indigo,$pt.X-4,$pt.Y-4,8,8)};$purplePen.Dispose();Close-Chart $capChart

$q3Chart=New-BaseChart (Join-Path $FiguresDir '<SOURCE_FILE_REDACTED>') '问题3：持续时间—排队长度' 'q=1500 pcu/h；三条曲线为不同事故断面能力' '事故持续时间 / min' '排队长度 / m'
$gc=$q3Chart.Graphics;$q3YMax=380.0
for($i=0;$i -le 5;$i++){ $y=$q3YMax*$i/5.0;$py=Map-Y $y 0 $q3YMax;$gc.DrawLine($q3Chart.GridPen,90,$py,1040,$py);$gc.DrawString($y.ToString('0',$Invariant),$q3Chart.SmallFont,[System.Drawing.Brushes]::DimGray,43,$py-7)}
for($i=0;$i -le 5;$i++){ $x=2.0*$i;$px=Map-X $x 0 10;$gc.DrawLine($q3Chart.GridPen,$px,90,$px,610);$gc.DrawString($x.ToString('0',$Invariant),$q3Chart.SmallFont,[System.Drawing.Brushes]::DimGray,$px-6,615)}
$colors=@([System.Drawing.Color]::SteelBlue,[System.Drawing.Color]::DarkOrange,[System.Drawing.Color]::SeaGreen);$caps=@(800.0,1000.0,1200.0)
for($j=0;$j -lt $caps.Count;$j++){ $rr=Get-KinematicResult $inflow $caps[$j] $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity;$pts=[System.Collections.Generic.List[System.Drawing.PointF]]::new();for($i=0;$i -le 100;$i++){$t=10.0*$i/100.0;$len=1000.0*[double]$rr.queue_growth_speed_km_h*$t/60.0;$pts.Add([System.Drawing.PointF]::new((Map-X $t 0 10),(Map-Y $len 0 $q3YMax)))};$pen=[System.Drawing.Pen]::new($colors[$j],4);$gc.DrawLines($pen,$pts.ToArray());$gc.DrawString(('C={0:0} pcu/h' -f $caps[$j]),$q3Chart.Font,[System.Drawing.SolidBrush]::new($colors[$j]),780,110+28*$j);$pen.Dispose()};Close-Chart $q3Chart

$laneChart=New-BaseChart (Join-Path $FiguresDir '<SOURCE_FILE_REDACTED>') '问题2：剩余车道的换道压力代理' '由题面转向比例计算；仅为条件性机理解释' '剩余车道原角色' '强制换道比例代理'
$gc=$laneChart.Graphics
for($i=0;$i -le 5;$i++){ $y=0.2*$i;$py=Map-Y $y 0 1;$gc.DrawLine($laneChart.GridPen,90,$py,1040,$py);$gc.DrawString($y.ToString('0.0',$Invariant),$laneChart.SmallFont,[System.Drawing.Brushes]::DimGray,45,$py-7)}
$laneOrder=@('right','through','left');for($i=0;$i -lt $laneOrder.Count;$i++){$row=$laneRows|Where-Object{$_.remaining_lane_role -eq $laneOrder[$i]};$x=260+300*$i;$barTop=Map-Y ([double]$row.mandatory_merge_proxy) 0 1;$height=610-$barTop;$gc.FillRectangle([System.Drawing.Brushes]::CadetBlue,$x-65,$barTop,130,$height);$gc.DrawString($laneOrder[$i],$laneChart.Font,[System.Drawing.Brushes]::Black,$x-35,620);$gc.DrawString(([double]$row.mandatory_merge_proxy).ToString('0.00',$Invariant),$laneChart.Font,[System.Drawing.Brushes]::Black,$x-25,$barTop-28)};Close-Chart $laneChart

$generatedFiles = @(
    'results/input_manifest.json', 'results/model_parameters_used.json', 'results/summary.json',
    'results/verification.json', 'results/<SOURCE_FILE_REDACTED>', 'results/<SOURCE_FILE_REDACTED>',
    'results/<SOURCE_FILE_REDACTED>', 'results/<SOURCE_FILE_REDACTED>', 'results/<SOURCE_FILE_REDACTED>',
    'results/<SOURCE_FILE_REDACTED>', 'results/<SOURCE_FILE_REDACTED>', 'results/<SOURCE_FILE_REDACTED>',
    'figures/<SOURCE_FILE_REDACTED>', 'figures/<SOURCE_FILE_REDACTED>',
    'figures/<SOURCE_FILE_REDACTED>', 'figures/<SOURCE_FILE_REDACTED>'
)
Write-JsonFile (Join-Path $ResultsDir 'generated-files.json') ([ordered]@{ schema_version=1; files=$generatedFiles; status='pass' })

Write-Output ('[PASS] deterministic model outputs generated; overall evidence status={0}' -f $summary.overall_status)
