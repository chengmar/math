Set-StrictMode -Version Latest

function Invoke-IndependentStorageBalance {
    <#
    Independent constant-state checker. It deliberately does not call the
    production kinematic/shock functions. Queue length is reconstructed from
    accumulated excess PCU divided by the independently calculated storage
    density jump.
    #>
    param(
        [double]$Inflow,
        [double]$Capacity,
        [double]$DistanceKm,
        [int]$Lanes,
        [double]$FreeSpeed,
        [double]$WaveSpeed,
        [double]$JamDensityPerLane,
        [double]$StepSeconds,
        [double]$MaxSeconds = 7200.0
    )

    if ($Inflow -lt 0 -or $Capacity -lt 0 -or $DistanceKm -lt 0 -or
        $Lanes -le 0 -or $FreeSpeed -le 0 -or $WaveSpeed -le 0 -or
        $JamDensityPerLane -le 0 -or $StepSeconds -le 0) {
        return [pscustomobject]@{ status = 'fail'; reason = 'invalid_parameter' }
    }

    $criticalCapacityPerLane = ($FreeSpeed * $WaveSpeed / ($FreeSpeed + $WaveSpeed)) * $JamDensityPerLane
    $upstreamCapacity = $Lanes * $criticalCapacityPerLane
    $arrivalDensity = $Inflow / $FreeSpeed
    $congestedDensity = $Lanes * $JamDensityPerLane - $Capacity / $WaveSpeed
    $storageDensity = $congestedDensity - $arrivalDensity
    if ($Inflow -gt $upstreamCapacity -or $Capacity -gt $upstreamCapacity -or $storageDensity -le 0) {
        return [pscustomobject]@{ status = 'fail'; reason = 'infeasible_fundamental_diagram_state' }
    }
    if ($DistanceKm -eq 0) {
        return [pscustomobject]@{
            status = 'pass'; regime = 'already_at_target'; hit_time_s = 0.0
            final_queue_length_km = 0.0; storage_density_pcu_km = $storageDensity
            mass_balance_error_pcu = 0.0
        }
    }
    if ($Inflow -le $Capacity) {
        return [pscustomobject]@{
            status = 'pass'; regime = 'no_growing_queue'; hit_time_s = $null
            final_queue_length_km = 0.0; storage_density_pcu_km = $storageDensity
            mass_balance_error_pcu = 0.0
        }
    }

    $elapsed = 0.0
    $excessPcu = 0.0
    $lengthKm = 0.0
    while ($lengthKm + 1e-15 -lt $DistanceKm -and $elapsed -lt $MaxSeconds) {
        $excessPcu += ($Inflow - $Capacity) * $StepSeconds / 3600.0
        $elapsed += $StepSeconds
        $lengthKm = $excessPcu / $storageDensity
    }
    if ($lengthKm + 1e-15 -lt $DistanceKm) {
        return [pscustomobject]@{
            status = 'needs_review'; regime = 'target_not_reached'; hit_time_s = $null
            final_queue_length_km = $lengthKm; storage_density_pcu_km = $storageDensity
            mass_balance_error_pcu = [Math]::Abs($excessPcu - $storageDensity * $lengthKm)
        }
    }
    return [pscustomobject]@{
        status = 'pass'; regime = 'target_reached'; hit_time_s = $elapsed
        final_queue_length_km = $lengthKm; storage_density_pcu_km = $storageDensity
        mass_balance_error_pcu = [Math]::Abs($excessPcu - $storageDensity * $lengthKm)
    }
}
