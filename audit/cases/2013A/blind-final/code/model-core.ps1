Set-StrictMode -Version Latest

function Round-Number {
    param([double]$Value, [int]$Digits = 6)
    return [Math]::Round($Value, $Digits, [MidpointRounding]::AwayFromZero)
}

function Get-Median {
    param([double[]]$Values)
    $sorted = @($Values | Sort-Object)
    if ($sorted.Count -eq 0) { return $null }
    $middle = [int][Math]::Floor($sorted.Count / 2)
    if ($sorted.Count % 2 -eq 1) { return [double]$sorted[$middle] }
    return ([double]$sorted[$middle - 1] + [double]$sorted[$middle]) / 2.0
}

function Get-Mad {
    param([double[]]$Values)
    if ($Values.Count -eq 0) { return $null }
    $median = Get-Median $Values
    return Get-Median ([double[]]@($Values | ForEach-Object { [Math]::Abs([double]$_ - $median) }))
}

function Get-KinematicStateRaw {
    param(
        [double]$Inflow,
        [double]$Capacity,
        [double]$DistanceKm,
        [int]$Lanes,
        [double]$FreeSpeed,
        [double]$WaveSpeed,
        [double]$JamDensityPerLane,
        [double]$NearThresholdGap = 0.001
    )

    if ($Inflow -lt 0 -or $Capacity -lt 0 -or $DistanceKm -lt 0 -or $Lanes -le 0 -or
        $FreeSpeed -le 0 -or $WaveSpeed -le 0 -or $JamDensityPerLane -le 0) {
        return [pscustomobject]@{ status = 'fail'; reason = 'invalid_parameter' }
    }
    $totalJam = $Lanes * $JamDensityPerLane
    $upstreamCapacity = $Lanes * ($FreeSpeed * $WaveSpeed / ($FreeSpeed + $WaveSpeed)) * $JamDensityPerLane
    $arrivalDensity = $Inflow / $FreeSpeed
    $congestedDensity = $totalJam - $Capacity / $WaveSpeed
    $densityJump = $congestedDensity - $arrivalDensity
    if ($densityJump -le 0 -or $Inflow -gt $upstreamCapacity -or $Capacity -gt $upstreamCapacity) {
        return [pscustomobject]@{
            status = 'fail'; reason = 'infeasible_fundamental_diagram_state'
            arrival_density_pcu_km_raw = $arrivalDensity
            congested_density_pcu_km_raw = $congestedDensity
            upstream_capacity_pcu_h_raw = $upstreamCapacity
        }
    }

    $signedRate = ($Inflow - $Capacity) / $densityJump
    $gap = [Math]::Abs($Inflow - $Capacity)
    $conditionIndicator = if ($gap -gt 0) { [Math]::Max($Inflow, $Capacity) / $gap } else { $null }
    $regime = if ($Inflow -gt $Capacity) { 'growing_queue' } elseif ($Inflow -lt $Capacity) { 'dissipating_if_present' } else { 'balanced' }
    $timeMinutes = if ($DistanceKm -eq 0) { 0.0 } elseif ($Inflow -gt $Capacity) { 60.0 * $DistanceKm / $signedRate } else { $null }
    $pointQueueMinutes = if ($DistanceKm -eq 0) { 0.0 } elseif ($Inflow -gt $Capacity) { 60.0 * $DistanceKm * $totalJam / ($Inflow - $Capacity) } else { $null }

    return [pscustomobject]@{
        status = 'pass'
        regime = $regime
        queue_grows_from_zero = ($Inflow -gt $Capacity)
        arrival_density_pcu_km_raw = $arrivalDensity
        congested_density_pcu_km_raw = $congestedDensity
        density_jump_pcu_km_raw = $densityJump
        signed_queue_rate_km_h_raw = $signedRate
        time_to_distance_min_raw = $timeMinutes
        point_queue_time_min_raw = $pointQueueMinutes
        upstream_capacity_pcu_h_raw = $upstreamCapacity
        near_capacity_threshold = ($gap -le $NearThresholdGap)
        capacity_gap_pcu_h_raw = $gap
        condition_indicator_raw = $conditionIndicator
    }
}

function Get-QueueLengthMRaw {
    param([double]$Minutes, $KinematicState)
    if ($KinematicState.status -ne 'pass' -or -not $KinematicState.queue_grows_from_zero) { return 0.0 }
    return 1000.0 * [double]$KinematicState.signed_queue_rate_km_h_raw * $Minutes / 60.0
}

function Invoke-PiecewiseShock {
    param(
        [object[]]$Segments,
        [double]$CycleSeconds,
        [double]$InitialPhaseSeconds,
        [double]$Capacity,
        [double]$DistanceKm,
        [int]$Lanes,
        [double]$FreeSpeed,
        [double]$WaveSpeed,
        [double]$JamDensityPerLane,
        [double]$MaxSeconds = 7200.0
    )

    if ($DistanceKm -eq 0) {
        return [pscustomobject]@{ status = 'pass'; hit_time_s_raw = 0.0; final_length_km_raw = 0.0; initial_phase_s = $InitialPhaseSeconds }
    }
    $lengthKm = 0.0
    $elapsed = 0.0
    $eventCount = 0
    while ($elapsed -lt $MaxSeconds) {
        $phase = ($InitialPhaseSeconds + $elapsed) % $CycleSeconds
        if ($phase -lt 0) { $phase += $CycleSeconds }
        if ([Math]::Abs($phase - $CycleSeconds) -lt 1e-9) { $phase = 0.0 }
        $segment = @($Segments | Where-Object {
            $phase + 1e-9 -ge [double]$_.start_s -and $phase -lt [double]$_.end_s - 1e-9
        } | Select-Object -First 1)
        if ($segment.Count -ne 1) {
            return [pscustomobject]@{ status = 'fail'; reason = 'schedule_gap_or_overlap'; phase_s = $phase }
        }
        $availableSeconds = [double]$segment[0].end_s - $phase
        if ($availableSeconds -le 1e-9) {
            $elapsed += 1e-9
            continue
        }
        if ($elapsed + $availableSeconds -gt $MaxSeconds) { $availableSeconds = $MaxSeconds - $elapsed }
        $state = Get-KinematicStateRaw -Inflow ([double]$segment[0].inflow_pcu_h) -Capacity $Capacity `
            -DistanceKm $DistanceKm -Lanes $Lanes -FreeSpeed $FreeSpeed -WaveSpeed $WaveSpeed `
            -JamDensityPerLane $JamDensityPerLane
        if ($state.status -ne 'pass') {
            return [pscustomobject]@{ status = 'fail'; reason = $state.reason; phase_s = $phase }
        }
        $rate = [double]$state.signed_queue_rate_km_h_raw
        $potentialLength = $lengthKm + $rate * $availableSeconds / 3600.0
        if ($rate -gt 0 -and $potentialLength + 1e-15 -ge $DistanceKm) {
            $partialSeconds = ($DistanceKm - $lengthKm) * 3600.0 / $rate
            return [pscustomobject]@{
                status = 'pass'; hit_time_s_raw = $elapsed + $partialSeconds
                final_length_km_raw = $DistanceKm; initial_phase_s = $InitialPhaseSeconds
                event_count = $eventCount + 1
            }
        }
        $lengthKm = [Math]::Max(0.0, $potentialLength)
        $elapsed += $availableSeconds
        $eventCount++
    }
    return [pscustomobject]@{
        status = 'needs_review'; reason = 'target_not_reached_within_horizon'
        hit_time_s_raw = $null; final_length_km_raw = $lengthKm
        initial_phase_s = $InitialPhaseSeconds; event_count = $eventCount
    }
}

function Get-RecordField {
    param($Record, [string]$Name, $Default = $null)
    $property = $Record.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        return $Default
    }
    return $property.Value
}

function ConvertTo-StrictBoolean {
    param($Value, [bool]$Default = $false)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $Default }
    $text = ([string]$Value).Trim().ToLowerInvariant()
    if ($text -in @('true','1','yes','y')) { return $true }
    if ($text -in @('false','0','no','n')) { return $false }
    throw "Invalid Boolean value: $Value"
}

function Convert-AnnotationRecord {
    param(
        $Record,
        $Weights,
        [double]$RequiredCycleSeconds = 60.0
    )

    $videoId = [string](Get-RecordField $Record 'video_id' '')
    $start = [double](Get-RecordField $Record 'interval_start_s' 0.0)
    $end = [double](Get-RecordField $Record 'interval_end_s' 0.0)
    $duration = $end - $start
    $passenger = [double](Get-RecordField $Record 'passenger_car_count' 0.0)
    $heavy = [double](Get-RecordField $Record 'bus_or_truck_count' 0.0)
    $electric = [double](Get-RecordField $Record 'electric_bicycle_count' 0.0)
    $complete = ConvertTo-StrictBoolean (Get-RecordField $Record 'complete_cycle' $false)
    $saturated = ConvertTo-StrictBoolean (Get-RecordField $Record 'saturated_demand' $false)
    $missingFrames = ConvertTo-StrictBoolean (Get-RecordField $Record 'missing_frame_flag' $false)
    $occlusion = ConvertTo-StrictBoolean (Get-RecordField $Record 'occlusion_flag' $false)
    $duplicate = ConvertTo-StrictBoolean (Get-RecordField $Record 'duplicate_count_flag' $false)
    $countsValid = ($passenger -ge 0 -and $heavy -ge 0 -and $electric -ge 0)
    $durationValid = ($duration -gt 0)
    $cycleValid = ($complete -and [Math]::Abs($duration - $RequiredCycleSeconds) -le 0.5)
    $qualityValid = (-not $missingFrames -and -not $occlusion -and -not $duplicate)
    $pcu = if ($countsValid) {
        $passenger * [double]$Weights.passenger_car + $heavy * [double]$Weights.bus_or_truck + $electric * [double]$Weights.electric_bicycle
    } else { $null }
    $flow = if ($durationValid -and $null -ne $pcu) { 3600.0 * [double]$pcu / $duration } else { $null }
    $isCapacity = ($durationValid -and $cycleValid -and $qualityValid -and $countsValid -and $saturated)
    $observationKind = if (-not $durationValid -or -not $countsValid -or -not $cycleValid -or -not $qualityValid) {
        'excluded_quality_or_cycle'
    } elseif (-not $saturated) {
        'observed_flow_capacity_lower_bound'
    } else {
        'capacity_candidate'
    }
    $rowStatus = if ($isCapacity) { 'pass' } else { 'needs_review' }
    return [pscustomobject]@{
        video_id = $videoId
        interval_start_s = $start
        interval_end_s = $end
        duration_s = $duration
        incident_phase = [string](Get-RecordField $Record 'incident_phase' '')
        occupied_lanes = [string](Get-RecordField $Record 'occupied_lanes' '')
        pcu_count = $pcu
        observed_flow_pcu_h = $flow
        capacity_pcu_h = if ($isCapacity) { $flow } else { $null }
        observation_kind = $observationKind
        complete_cycle = $complete
        saturated_demand = $saturated
        quality_flags_clear = $qualityValid
        status = $rowStatus
        rolling_three_cycle_median_pcu_h = $null
        segment_id = $null
    }
}

function Invoke-CapacityPipeline {
    param(
        [object[]]$Records,
        $Weights,
        [double]$RequiredCycleSeconds = 60.0
    )

    $rows = @($Records | ForEach-Object { Convert-AnnotationRecord -Record $_ -Weights $Weights -RequiredCycleSeconds $RequiredCycleSeconds })
    $segmentRows = @()
    foreach ($videoGroup in @($rows | Group-Object video_id)) {
        $valid = @($videoGroup.Group | Where-Object { $_.status -eq 'pass' } | Sort-Object interval_start_s)
        for ($i = 0; $i -lt $valid.Count; $i++) {
            $from = [Math]::Max(0, $i - 2)
            $window = [double[]]@($valid[$from..$i] | ForEach-Object { [double]$_.capacity_pcu_h })
            $valid[$i].rolling_three_cycle_median_pcu_h = Get-Median $window
        }
        $segmentId = 1
        $segmentSmooth = [System.Collections.Generic.List[double]]::new()
        for ($i = 0; $i -lt $valid.Count; $i++) {
            $current = [double]$valid[$i].rolling_three_cycle_median_pcu_h
            if ($i -ge 3 -and $segmentSmooth.Count -gt 0) {
                $reference = Get-Median $segmentSmooth.ToArray()
                $mad = Get-Mad $segmentSmooth.ToArray()
                $threshold = [Math]::Max(50.0, [Math]::Max(0.15 * [Math]::Abs($reference), 3.0 * 1.4826 * $mad))
                if ([Math]::Abs($current - $reference) -gt $threshold) {
                    $segmentId++
                    $segmentSmooth.Clear()
                }
            }
            $valid[$i].segment_id = $segmentId
            $segmentSmooth.Add($current)
        }
        foreach ($segmentGroup in @($valid | Group-Object segment_id)) {
            $values = [double[]]@($segmentGroup.Group | ForEach-Object { [double]$_.capacity_pcu_h })
            $segmentRows += [pscustomobject]@{
                video_id = $videoGroup.Name
                segment_id = [int]$segmentGroup.Name
                interval_start_s = ($segmentGroup.Group | Measure-Object interval_start_s -Minimum).Minimum
                interval_end_s = ($segmentGroup.Group | Measure-Object interval_end_s -Maximum).Maximum
                valid_cycle_count = $segmentGroup.Count
                median_capacity_pcu_h = Get-Median $values
                mad_capacity_pcu_h = Get-Mad $values
                status = 'pass'
            }
        }
    }
    return [pscustomobject]@{ rows = $rows; segments = $segmentRows }
}
