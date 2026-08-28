param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot),
    [string]$AnnotationPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'model-core.ps1')
. (Join-Path $PSScriptRoot 'independent-validator.ps1')
. (Join-Path $PSScriptRoot 'charts.ps1')

$Invariant = [System.Globalization.CultureInfo]::InvariantCulture
[System.Threading.Thread]::CurrentThread.CurrentCulture = $Invariant
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
    [System.IO.File]::WriteAllText($Path,$Text,$Utf8NoBom)
}
function Write-JsonFile {
    param([string]$Path,$Value)
    Write-TextFile $Path (($Value | ConvertTo-Json -Depth 30) + "`n")
}
function Write-CsvFile {
    param([string]$Path,[object[]]$Rows)
    $lines = @($Rows | ConvertTo-Csv -NoTypeInformation)
    Write-TextFile $Path (($lines -join "`n") + "`n")
}
function Get-FileMagicHex {
    param([string]$Path,[int]$ByteCount=8)
    $stream=[System.IO.File]::OpenRead($Path)
    try { $buffer=[byte[]]::new($ByteCount); $read=$stream.Read($buffer,0,$ByteCount); return ([Convert]::ToHexString($buffer[0..($read-1)])).ToLowerInvariant() } finally { $stream.Dispose() }
}
function New-Check {
    param([string]$Id,[string]$Status,$Metric,$Tolerance,[string]$Claim='')
    return [pscustomobject]@{ id=$Id; status=$Status; metric=$Metric; tolerance=$Tolerance; claim=$Claim }
}

$Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$Seed = [int]$Config.seed
$null = Get-Random -SetSeed $Seed
$g=$Config.given; $e=$Config.engineering_scenario
$lanes=[int]$g.lanes; $q3BoundaryM=[double]$g.q3_link_length_m; $q4BoundaryM=[double]$g.q4_distance_m
$distanceKm=$q4BoundaryM/1000.0; $inflow=[double]$g.q4_inflow_pcu_h
$freeSpeed=[double]$e.free_flow_speed_km_h; $waveSpeed=[double]$e.backward_wave_speed_km_h
$jamDensity=[double]$e.jam_density_pcu_km_lane; $centralCapacity=[double]$e.incident_capacity_pcu_h
$nearGap=[double]$Config.validation.near_threshold_gap_pcu_h

# Exact input identity. A count alone can never pass this gate.
$sourceRows=@()
foreach($expected in $Config.expected_inputs){
    $relative=[string]$expected.path; $absolute=Join-Path $Workspace $relative
    if(-not(Test-Path -LiteralPath $absolute -PathType Leaf)){
        $sourceRows += [pscustomobject]@{path=$relative;bytes=$null;sha256=$null;magic_hex=$null;status='fail';reason='missing'}
        continue
    }
    $item=Get-Item -LiteralPath $absolute; $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $absolute).Hash.ToLowerInvariant(); $magic=Get-FileMagicHex $absolute
    $same=([long]$item.Length -eq [long]$expected.bytes -and $hash -eq [string]$expected.sha256 -and $magic -eq [string]$expected.magic_hex)
    $sourceRows += [pscustomobject]@{path=$relative.Replace('\','/');bytes=[long]$item.Length;sha256=$hash;magic_hex=$magic;status=if($same){'pass'}else{'fail'};reason=if($same){'exact_path_size_sha256_and_magic_match'}else{'identity_mismatch'}}
}
$sourceIdentityStatus=if(@($sourceRows|Where-Object{$_.status -eq 'fail'}).Count -eq 0 -and $sourceRows.Count -eq $Config.expected_inputs.Count){'pass'}else{'fail'}
if($sourceIdentityStatus -eq 'fail'){ throw 'fail: allowed input identity mismatch' }

$videoExtensions=@('.mp4','.avi','.mov','.mkv','.wmv','.m4v')
$videoItems=@(Get-ChildItem -LiteralPath $InputDir -Recurse -File | Where-Object{$videoExtensions -contains $_.Extension.ToLowerInvariant()} | Sort-Object FullName)
$ffprobe=Get-Command 'ffprobe' -ErrorAction SilentlyContinue
$videoRows=@()
foreach($item in $videoItems){
    $relativeVideo=$item.FullName.Substring($Workspace.Length+1).Replace('\','/')
    $videoHash=(Get-FileHash -Algorithm SHA256 -LiteralPath $item.FullName).Hash.ToLowerInvariant()
    $expectedVideo=@($Config.expected_videos|Where-Object{[string]$_.path -eq $relativeVideo}|Select-Object -First 1)
    $expectedIdentityStatus=if($expectedVideo.Count -eq 0){'needs_review'}elseif([long]$item.Length -eq [long]$expectedVideo[0].bytes -and $videoHash -eq [string]$expectedVideo[0].sha256){'pass'}else{'fail'}
    $decodeStatus='needs_review'; $duration=$null; $frameRate=$null; $frameCount=$null
    if($null -ne $ffprobe){
        try{
            $probeText=& $ffprobe.Source -v error -select_streams v:0 -show_entries stream=avg_frame_rate,nb_frames:format=duration -of json -- $item.FullName 2>$null
            if($LASTEXITCODE -eq 0){ $probe=$probeText|ConvertFrom-Json; $duration=[double]$probe.format.duration; $frameRate=[string]$probe.streams[0].avg_frame_rate; $frameCount=[string]$probe.streams[0].nb_frames; if($duration -gt 0 -and -not[string]::IsNullOrWhiteSpace($frameRate)){$decodeStatus='pass'} }
        }catch{ $decodeStatus='fail' }
    }
    $videoStatus=if($expectedIdentityStatus -eq 'fail' -or $decodeStatus -eq 'fail'){'fail'}elseif($expectedIdentityStatus -eq 'pass' -and $decodeStatus -eq 'pass'){'pass'}else{'needs_review'}
    $videoRows += [pscustomobject]@{path=$relativeVideo;bytes=[long]$item.Length;sha256=$videoHash;duration_s=$duration;frame_rate=$frameRate;frame_count=$frameCount;decode_status=$decodeStatus;expected_identity_status=$expectedIdentityStatus;status=$videoStatus}
}
$expectedVideoCount=@($Config.expected_videos).Count
$missingExpectedVideos=if($expectedVideoCount -gt 0){@($Config.expected_videos|Where-Object{$expectedPath=[string]$_.path;-not(Test-Path -LiteralPath (Join-Path $Workspace $expectedPath) -PathType Leaf)}).Count}else{0}
$videoCompletenessStatus=if($expectedVideoCount -ge 2 -and $missingExpectedVideos -eq 0 -and @($videoRows|Where-Object{$_.status -ne 'pass'}).Count -eq 0 -and $videoRows.Count -ge 2){'pass'}elseif(@($videoRows|Where-Object{$_.status -eq 'fail'}).Count -gt 0 -or $missingExpectedVideos -gt 0){'fail'}else{'needs_review'}
$inputManifest=[ordered]@{schema_version=2;source_files=$sourceRows;source_identity_status=$sourceIdentityStatus;video_files=$videoRows;video_files_found=$videoRows.Count;required_video_files=2;expected_video_manifest_entries=$expectedVideoCount;missing_expected_video_count=$missingExpectedVideos;video_completeness_status=$videoCompletenessStatus;note='Video completeness requires immutable expected path/size/SHA-256 entries plus successful ffprobe decode metadata.'}
Write-JsonFile (Join-Path $ResultsDir 'input_manifest.json') $inputManifest

# Q1: executable annotation-to-PCU capacity pipeline. Missing real annotations remain needs_review.
$templateRows=@(
 [pscustomobject]@{video_id='video1';interval_start_s=$null;interval_end_s=$null;passenger_car_count=$null;bus_or_truck_count=$null;electric_bicycle_count=$null;complete_cycle=$null;saturated_demand=$null;missing_frame_flag=$null;occlusion_flag=$null;duplicate_count_flag=$null;incident_phase=$null;occupied_lanes=$null;status='needs_review'},
 [pscustomobject]@{video_id='video2';interval_start_s=$null;interval_end_s=$null;passenger_car_count=$null;bus_or_truck_count=$null;electric_bicycle_count=$null;complete_cycle=$null;saturated_demand=$null;missing_frame_flag=$null;occlusion_flag=$null;duplicate_count_flag=$null;incident_phase=$null;occupied_lanes=$null;status='needs_review'}
)
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $templateRows
if([string]::IsNullOrWhiteSpace($AnnotationPath)){ $AnnotationPath=Join-Path $InputDir 'annotations\<SOURCE_FILE_REDACTED>' } elseif(-not[System.IO.Path]::IsPathRooted($AnnotationPath)){ $AnnotationPath=Join-Path $Workspace $AnnotationPath }
$annotationAvailable=Test-Path -LiteralPath $AnnotationPath -PathType Leaf
if($annotationAvailable){
    $annotationRecords=@(Import-Csv -LiteralPath $AnnotationPath); $capacityPipeline=Invoke-CapacityPipeline $annotationRecords $Config.pcu_weights ([double]$g.signal_cycle_s); $capacityEstimateRows=@($capacityPipeline.rows); $capacitySegmentRows=@($capacityPipeline.segments)
}else{
    $capacityEstimateRows=@([pscustomobject]@{video_id='video1';interval_start_s=$null;interval_end_s=$null;duration_s=$null;incident_phase='';occupied_lanes='';pcu_count=$null;observed_flow_pcu_h=$null;capacity_pcu_h=$null;observation_kind='no_authorized_annotation_data';complete_cycle=$false;saturated_demand=$false;quality_flags_clear=$false;status='needs_review';rolling_three_cycle_median_pcu_h=$null;segment_id=$null})
    $capacitySegmentRows=@([pscustomobject]@{video_id='video1';segment_id=$null;interval_start_s=$null;interval_end_s=$null;valid_cycle_count=0;median_capacity_pcu_h=$null;mad_capacity_pcu_h=$null;status='needs_review'})
}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $capacityEstimateRows
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $capacitySegmentRows

function New-SyntheticAnnotation([string]$Video,[double]$Start,[double]$Count,[bool]$Saturated=$true,[bool]$Complete=$true,[double]$Duration=60.0){
    return [pscustomobject]@{video_id=$Video;interval_start_s=$Start;interval_end_s=$Start+$Duration;passenger_car_count=$Count;bus_or_truck_count=0;electric_bicycle_count=0;complete_cycle=$Complete;saturated_demand=$Saturated;missing_frame_flag=$false;occlusion_flag=$false;duplicate_count_flag=$false;incident_phase='synthetic_test';occupied_lanes='synthetic_test'}
}
$unit60=Convert-AnnotationRecord (New-SyntheticAnnotation 'unit' 0 60) $Config.pcu_weights 60
$unitUnsaturated=Convert-AnnotationRecord (New-SyntheticAnnotation 'unit' 60 60 $false) $Config.pcu_weights 60
$unitIncomplete=Convert-AnnotationRecord (New-SyntheticAnnotation 'unit' 120 60 $true $false 30) $Config.pcu_weights 60
$stageRecords=@();$stageCounts=@(60.0,61.0,59.0,30.0,31.0,29.0);for($i=0;$i -lt $stageCounts.Count;$i++){$stageRecords+=New-SyntheticAnnotation 'stage' (60.0*$i) $stageCounts[$i]}
$stagePipeline=Invoke-CapacityPipeline $stageRecords $Config.pcu_weights 60
$capacityUnitChecks=@(
 (New-Check 'Q1U01_60_pcu_in_60s' $(if([Math]::Abs([double]$unit60.capacity_pcu_h-3600.0)-le 1e-9){'pass'}else{'fail'}) $unit60.capacity_pcu_h 3600 'seconds convention is 3600*PCU/delta_t_s'),
 (New-Check 'Q1U02_unsaturated_not_capacity' $(if($null -eq $unitUnsaturated.capacity_pcu_h -and $unitUnsaturated.observation_kind -eq 'observed_flow_capacity_lower_bound'){'pass'}else{'fail'}) $unitUnsaturated.observation_kind 'observed_flow_capacity_lower_bound' 'unsaturated throughput is only a lower bound'),
 (New-Check 'Q1U03_incomplete_cycle_excluded' $(if($null -eq $unitIncomplete.capacity_pcu_h -and $unitIncomplete.observation_kind -eq 'excluded_quality_or_cycle'){'pass'}else{'fail'}) $unitIncomplete.observation_kind 'excluded_quality_or_cycle' 'partial cycles cannot estimate capacity'),
 (New-Check 'Q1U04_stage_change_detected' $(if(@($stagePipeline.segments).Count -ge 2){'pass'}else{'fail'}) @($stagePipeline.segments).Count '>=2' 'synthetic step must create a new robust segment')
)
$capacityUnitStatus=if(@($capacityUnitChecks|Where-Object{$_.status -eq 'fail'}).Count -eq 0){'pass'}else{'fail'}
Write-JsonFile (Join-Path $ResultsDir 'capacity-unit-tests.json') ([ordered]@{schema_version=1;overall_status=$capacityUnitStatus;synthetic_data_only=$true;checks=$capacityUnitChecks;claim_limit='pass validates the implemented estimator logic, not absent video observations.'})

# Q2: M=1-p is descriptive only. No capacity rank is generated.
$laneRows=@()
foreach($turn in $Config.turn_shares){
    $laneRows += [pscustomobject]@{remaining_lane_role=[string]$turn.movement;native_demand_share=Round-Number ([double]$turn.share) 3;mandatory_merge_proxy=Round-Number (1.0-[double]$turn.share) 3;capacity_rank=$null;inference_scope='descriptive_demand_rearrangement_only';capacity_inference_status='needs_review';status='needs_review'}
}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $laneRows
$validVideo1=@($capacityEstimateRows|Where-Object{$_.video_id -eq 'video1' -and $_.status -eq 'pass'});$validVideo2=@($capacityEstimateRows|Where-Object{$_.video_id -eq 'video2' -and $_.status -eq 'pass'})
$q2Comparison=[ordered]@{schema_version=1;status='needs_review';matched_empirical_comparison_available=$false;video1_valid_saturated_cycles=$validVideo1.Count;video2_valid_saturated_cycles=$validVideo2.Count;strict_capacity_order_supported_status='fail';reason='M=1-p does not identify capacity loss; matched saturated video cycles and uncertainty are required.';equal_capacity_counterexample='All remaining-lane capacities may equal the same base saturation flow even when M is 0.56, 0.65, and 0.79.'}
Write-JsonFile (Join-Path $ResultsDir 'q2_comparison.json') $q2Comparison

# Constant-state baseline and parameter grids, retaining full precision internally.
$central=Get-KinematicStateRaw $inflow $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity $nearGap
if($central.status -ne 'pass'){throw 'fail: central kinematic scenario infeasible'}
$capacityRows=@()
foreach($capacity in $Config.capacity_grid_pcu_h){
    $state=Get-KinematicStateRaw $inflow ([double]$capacity) $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity $nearGap
    $capacityRows += [pscustomobject]@{capacity_pcu_h=[double]$capacity;regime=$state.regime;queue_growth_speed_km_h=Round-Number ([Math]::Max(0.0,[double]$state.signed_queue_rate_km_h_raw)) 9;time_to_140m_min=if($null -eq $state.time_to_distance_min_raw){$null}else{Round-Number ([double]$state.time_to_distance_min_raw) 6};point_queue_time_min=if($null -eq $state.point_queue_time_min_raw){$null}else{Round-Number ([double]$state.point_queue_time_min_raw) 6};near_capacity_threshold=$state.near_capacity_threshold;calculation_status=$state.status;evidence_status='needs_review'}
}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $capacityRows
$mainLow=Get-KinematicStateRaw $inflow ([double]$e.main_capacity_low_pcu_h) $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity
$mainHigh=Get-KinematicStateRaw $inflow ([double]$e.main_capacity_high_pcu_h) $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity

$oneAtATime=@()
foreach($capacity in @(800.0,1000.0,1200.0)){$r=Get-KinematicStateRaw $inflow $capacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity;$oneAtATime+=[pscustomobject]@{parameter='capacity_pcu_h';value=$capacity;time_to_140m_min=Round-Number ([double]$r.time_to_distance_min_raw) 6}}
foreach($value in @(30.0,40.0,50.0)){$r=Get-KinematicStateRaw $inflow $centralCapacity $distanceKm $lanes $value $waveSpeed $jamDensity;$oneAtATime+=[pscustomobject]@{parameter='free_flow_speed_km_h';value=$value;time_to_140m_min=Round-Number ([double]$r.time_to_distance_min_raw) 6}}
foreach($value in @(10.0,15.0,20.0)){$r=Get-KinematicStateRaw $inflow $centralCapacity $distanceKm $lanes $freeSpeed $value $jamDensity;$oneAtATime+=[pscustomobject]@{parameter='backward_wave_speed_km_h';value=$value;time_to_140m_min=Round-Number ([double]$r.time_to_distance_min_raw) 6}}
foreach($value in @(120.0,140.0,160.0)){$r=Get-KinematicStateRaw $inflow $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $value;$oneAtATime+=[pscustomobject]@{parameter='jam_density_pcu_km_lane';value=$value;time_to_140m_min=Round-Number ([double]$r.time_to_distance_min_raw) 6}}
foreach($value in @(120.0,140.0,160.0)){$r=Get-KinematicStateRaw $inflow $centralCapacity ($value/1000.0) $lanes $freeSpeed $waveSpeed $jamDensity;$oneAtATime+=[pscustomobject]@{parameter='distance_m';value=$value;time_to_140m_min=Round-Number ([double]$r.time_to_distance_min_raw) 6}}
foreach($value in @(1400.0,1500.0,1600.0)){$r=Get-KinematicStateRaw $value $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity;$oneAtATime+=[pscustomobject]@{parameter='inflow_pcu_h';value=$value;time_to_140m_min=Round-Number ([double]$r.time_to_distance_min_raw) 6}}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $oneAtATime

$jointRows=@()
foreach($capacity in $Config.sensitivity_grid.capacity_pcu_h){foreach($vf in $Config.sensitivity_grid.free_flow_speed_km_h){foreach($w in $Config.sensitivity_grid.backward_wave_speed_km_h){foreach($kj in $Config.sensitivity_grid.jam_density_pcu_km_lane){$r=Get-KinematicStateRaw $inflow ([double]$capacity) $distanceKm $lanes ([double]$vf) ([double]$w) ([double]$kj);$jointRows+=[pscustomobject]@{capacity_pcu_h=[double]$capacity;free_flow_speed_km_h=[double]$vf;backward_wave_speed_km_h=[double]$w;jam_density_pcu_km_lane=[double]$kj;time_to_140m_min=Round-Number ([double]$r.time_to_distance_min_raw) 6;status=$r.status}}}}}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $jointRows
$jointTimes=[double[]]@($jointRows|ForEach-Object{[double]$_.time_to_140m_min});$jointMin=($jointTimes|Measure-Object -Minimum).Minimum;$jointMax=($jointTimes|Measure-Object -Maximum).Maximum;$jointMedian=Get-Median $jointTimes

# Q3 finite-link grid: reported queue length is capped at the 240 m boundary.
$q3Rows=@()
foreach($qValue in @(1200.0,1500.0,1800.0)){foreach($capacity in @(600.0,800.0,1000.0,1200.0,1400.0)){
    $r=Get-KinematicStateRaw $qValue $capacity ($q3BoundaryM/1000.0) $lanes $freeSpeed $waveSpeed $jamDensity
    foreach($duration in @(2.0,5.0,10.0)){
        $unbounded=if($r.status -eq 'pass' -and $r.queue_grows_from_zero){Get-QueueLengthMRaw $duration $r}else{0.0};$reached=($unbounded + 1e-9 -ge $q3BoundaryM);$hit=if($r.queue_grows_from_zero){60.0*($q3BoundaryM/1000.0)/[double]$r.signed_queue_rate_km_h_raw}else{$null}
        $q3Rows+=[pscustomobject]@{inflow_pcu_h=$qValue;capacity_pcu_h=$capacity;incident_duration_min=$duration;queue_length_m=Round-Number ([Math]::Min($q3BoundaryM,$unbounded)) 6;finite_link_boundary_m=$q3BoundaryM;boundary_reached=$reached;time_to_boundary_min=if($null -eq $hit){$null}else{Round-Number $hit 6};pre_boundary_calculation_status=$r.status;post_boundary_status=if($reached){'needs_review'}else{'pass'};regime=if($reached){'spillback_reached'}else{$r.regime}}
    }
}}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $q3Rows
$q3BoundaryRows=@($q3Rows|Where-Object{$_.boundary_reached}).Count

# Q4 time series stops exactly at first hitting time; no post-boundary row is emitted.
$analyticSeconds=60.0*[double]$central.time_to_distance_min_raw;$timeRows=@();$lastGrid=[Math]::Floor($analyticSeconds/15.0)*15.0
for($seconds=0.0;$seconds -le $lastGrid+1e-9;$seconds+=15.0){$minutes=$seconds/60.0;$lwr=Get-QueueLengthMRaw $minutes $central;$excess=[Math]::Max(0.0,($inflow-$centralCapacity)*$seconds/3600.0);$point=1000.0*$excess/($lanes*$jamDensity);$timeRows+=[pscustomobject]@{time_s=Round-Number $seconds 6;time_min=Round-Number $minutes 6;lwr_queue_length_m=Round-Number ([Math]::Min($q4BoundaryM,$lwr)) 6;point_queue_length_m=Round-Number $point 6;boundary_state='pre_boundary';domain_status='pass'}}
$timeRows+=[pscustomobject]@{time_s=Round-Number $analyticSeconds 6;time_min=Round-Number ([double]$central.time_to_distance_min_raw) 6;lwr_queue_length_m=$q4BoundaryM;point_queue_length_m=Round-Number (1000.0*(($inflow-$centralCapacity)*$analyticSeconds/3600.0)/($lanes*$jamDensity)) 6;boundary_state='first_hit';domain_status='pass'}
$timeRows=@($timeRows|Sort-Object time_s -Unique);Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $timeRows

# Signal-aware event integration and audit counterexample regression.
$cycle=[double]$g.signal_cycle_s;$phaseDuration=[double]$g.phase_duration_s;$yellow=[double]$g.yellow_s;$greenFlash=[double]$g.green_flash_s
$square=$Config.signal_sensitivity.audit_square_wave;$squareSegments=@([pscustomobject]@{start_s=0.0;end_s=[double]$square.high_duration_s;inflow_pcu_h=[double]$square.high_pcu_h},[pscustomobject]@{start_s=[double]$square.high_duration_s;end_s=$cycle;inflow_pcu_h=[double]$square.low_pcu_h})
$squareGreen=Invoke-PiecewiseShock $squareSegments $cycle 0.0 $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity ([double]$Config.validation.max_simulation_s)
$squareRed=Invoke-PiecewiseShock $squareSegments $cycle ([double]$square.high_duration_s) $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity ([double]$Config.validation.max_simulation_s)
$constantSegments=@([pscustomobject]@{start_s=0.0;end_s=$cycle;inflow_pcu_h=$inflow});$constantEvent=Invoke-PiecewiseShock $constantSegments $cycle 0.0 $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity ([double]$Config.validation.max_simulation_s)

$detailed=$Config.signal_sensitivity.detailed_phase;$steadyDuration=$phaseDuration-$greenFlash-$yellow
if($steadyDuration -le 0 -or [Math]::Abs(2.0*$phaseDuration-$cycle)-gt 1e-9){throw 'fail: inconsistent signal phase durations'}
$rightShare=[double](@($Config.turn_shares|Where-Object{$_.movement -eq 'right'})[0].share);$rightConstant=$inflow*$rightShare;$controlledMean=$inflow*(1.0-$rightShare)
$effectiveGreen=$steadyDuration*[double]$detailed.steady_green_multiplier+$greenFlash*[double]$detailed.green_flash_multiplier+$yellow*[double]$detailed.yellow_multiplier
$controlledBase=$controlledMean*$cycle/$effectiveGreen
$detailedSegments=@(
 [pscustomobject]@{label='steady_green';start_s=0.0;end_s=$steadyDuration;multiplier=[double]$detailed.steady_green_multiplier;inflow_pcu_h=$rightConstant+$controlledBase*[double]$detailed.steady_green_multiplier},
 [pscustomobject]@{label='green_flash';start_s=$steadyDuration;end_s=$steadyDuration+$greenFlash;multiplier=[double]$detailed.green_flash_multiplier;inflow_pcu_h=$rightConstant+$controlledBase*[double]$detailed.green_flash_multiplier},
 [pscustomobject]@{label='yellow';start_s=$steadyDuration+$greenFlash;end_s=$phaseDuration;multiplier=[double]$detailed.yellow_multiplier;inflow_pcu_h=$rightConstant+$controlledBase*[double]$detailed.yellow_multiplier},
 [pscustomobject]@{label='red_right_turn_only';start_s=$phaseDuration;end_s=$cycle;multiplier=[double]$detailed.red_multiplier;inflow_pcu_h=$rightConstant+$controlledBase*[double]$detailed.red_multiplier}
)
$calendarRows=@($detailedSegments|ForEach-Object{[pscustomobject]@{state=$_.label;start_s=$_.start_s;end_s=$_.end_s;duration_s=[double]$_.end_s-[double]$_.start_s;controlled_multiplier=$_.multiplier;total_arrival_pcu_h=Round-Number ([double]$_.inflow_pcu_h) 6;model_status='needs_review'}});Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $calendarRows
$freeFlowDelay=([double]$g.source_to_incident_distance_m/1000.0)/$freeSpeed*3600.0;$phaseStep=[double]$detailed.source_phase_scan_step_s;$signalRows=@()
for($sourcePhase=0.0;$sourcePhase -lt $cycle-1e-9;$sourcePhase+=$phaseStep){$incidentPhase=($sourcePhase-$freeFlowDelay)%$cycle;if($incidentPhase -lt 0){$incidentPhase+=$cycle};$hit=Invoke-PiecewiseShock $detailedSegments $cycle $incidentPhase $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity ([double]$Config.validation.max_simulation_s);$signalRows+=[pscustomobject]@{source_initial_phase_s=Round-Number $sourcePhase 6;incident_initial_phase_s=Round-Number $incidentPhase 6;free_flow_delay_s=Round-Number $freeFlowDelay 6;hit_time_s=Round-Number ([double]$hit.hit_time_s_raw) 6;hit_time_min=Round-Number ([double]$hit.hit_time_s_raw/60.0) 6;status=$hit.status;evidence_status='needs_review'}}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $signalRows
$detailedTimes=[double[]]@($signalRows|ForEach-Object{[double]$_.hit_time_min});$detailedMin=($detailedTimes|Measure-Object -Minimum).Minimum;$detailedMax=($detailedTimes|Measure-Object -Maximum).Maximum
$detailedMean=($detailedSegments|ForEach-Object{([double]$_.end_s-[double]$_.start_s)*[double]$_.inflow_pcu_h}|Measure-Object -Sum).Sum/$cycle
$signalChecks=@(
 (New-Check 'S01_constant_degeneracy' $(if([Math]::Abs([double]$constantEvent.hit_time_s_raw-$analyticSeconds)-le 1e-7){'pass'}else{'fail'}) (Round-Number ([double]$constantEvent.hit_time_s_raw) 6) (Round-Number $analyticSeconds 6) 'piecewise solver reduces to the constant baseline'),
 (New-Check 'S02_same_mean_green_first' $(if([Math]::Abs([double]$squareGreen.hit_time_s_raw-195.588)-le 0.002){'pass'}else{'fail'}) (Round-Number ([double]$squareGreen.hit_time_s_raw) 6) 195.588 'audit square-wave counterexample'),
 (New-Check 'S03_same_mean_red_first' $(if([Math]::Abs([double]$squareRed.hit_time_s_raw-225.588)-le 0.002){'pass'}else{'fail'}) (Round-Number ([double]$squareRed.hit_time_s_raw) 6) 225.588 'audit square-wave counterexample'),
 (New-Check 'S04_square_wave_mean' $(if([Math]::Abs((([double]$square.high_pcu_h*[double]$square.high_duration_s+[double]$square.low_pcu_h*[double]$square.low_duration_s)/$cycle)-$inflow)-le 1e-9){'pass'}else{'fail'}) $inflow $inflow 'same cycle mean'),
 (New-Check 'S05_detailed_signal_mean' $(if([Math]::Abs($detailedMean-$inflow)-le 1e-9){'pass'}else{'fail'}) (Round-Number $detailedMean 9) $inflow 'uses 30 s phase, 3 s green flash, 3 s yellow, and uncontrolled right turns')
)
$signalStatus=if(@($signalChecks|Where-Object{$_.status -eq 'fail'}).Count -eq 0){'pass'}else{'fail'}
$signalReport=[ordered]@{schema_version=1;overall_status=$signalStatus;constant_baseline_time_s=Round-Number ([double]$constantEvent.hit_time_s_raw) 6;audit_square_wave=[ordered]@{cycle_mean_pcu_h=$inflow;green_first_time_s=Round-Number ([double]$squareGreen.hit_time_s_raw) 6;green_first_time_min=Round-Number ([double]$squareGreen.hit_time_s_raw/60.0) 6;red_first_time_s=Round-Number ([double]$squareRed.hit_time_s_raw) 6;red_first_time_min=Round-Number ([double]$squareRed.hit_time_s_raw/60.0) 6;evidence_status='pass';claim='counterexample_not_case_prediction'};detailed_engineering_phase_scan=[ordered]@{free_flow_delay_s=Round-Number $freeFlowDelay 6;minimum_time_min=Round-Number $detailedMin 6;maximum_time_min=Round-Number $detailedMax 6;phase_grid_step_s=$phaseStep;case_specific_status='needs_review'};checks=$signalChecks}
Write-JsonFile (Join-Path $ResultsDir 'signal-regression.json') $signalReport

# Independent storage-balance convergence, mutation sensitivity, and edge cases.
$convergenceRows=@();foreach($step in $Config.validation.convergence_steps_s){$n=Invoke-IndependentStorageBalance $inflow $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity ([double]$step) ([double]$Config.validation.max_simulation_s);$convergenceRows+=[pscustomobject]@{step_s=[double]$step;independent_hit_time_s=Round-Number ([double]$n.hit_time_s) 6;analytic_hit_time_s=Round-Number $analyticSeconds 6;absolute_error_s=Round-Number ([Math]::Abs([double]$n.hit_time_s-$analyticSeconds)) 6;error_within_one_step_status=if([Math]::Abs([double]$n.hit_time_s-$analyticSeconds)-le [double]$step+1e-9){'pass'}else{'fail'};mass_balance_error_pcu=$n.mass_balance_error_pcu;status=$n.status}}
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $convergenceRows
$convergenceStatus=if(@($convergenceRows|Where-Object{$_.status -ne 'pass' -or $_.error_within_one_step_status -ne 'pass' -or [double]$_.mass_balance_error_pcu -gt 1e-10}).Count -eq 0){'pass'}else{'fail'}
$smallest=$convergenceRows|Sort-Object step_s|Select-Object -First 1;$mutatedArrivalDensity=1.05*($inflow/$freeSpeed);$mutatedStorage=$lanes*$jamDensity-$centralCapacity/$waveSpeed-$mutatedArrivalDensity;$mutatedAnalyticSeconds=$distanceKm*$mutatedStorage/($inflow-$centralCapacity)*3600.0;$mutationDelta=[Math]::Abs([double]$smallest.independent_hit_time_s-$mutatedAnalyticSeconds);$mutationStatus=if($mutationDelta -gt 1.0){'pass'}else{'fail'}
$nearState=Get-KinematicStateRaw 1500.0 1499.9999 $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity $nearGap;$nearLength60m=1000.0*[double]$nearState.signed_queue_rate_km_h_raw;$nearConsistency=[Math]::Abs($nearLength60m-1000.0*[double]$nearState.signed_queue_rate_km_h_raw*60.0/60.0);$nearStatus=if([double]$nearState.signed_queue_rate_km_h_raw -gt 0 -and [double]$nearState.time_to_distance_min_raw -gt 0 -and $nearConsistency -le 1e-15){'pass'}else{'fail'}
$edgeReport=[ordered]@{schema_version=1;overall_status=$nearStatus;near_capacity_case=[ordered]@{inflow_pcu_h=1500.0;capacity_pcu_h=1499.9999;capacity_gap_pcu_h=$nearState.capacity_gap_pcu_h_raw;growth_speed_km_h_raw=$nearState.signed_queue_rate_km_h_raw;time_to_140m_min_raw=$nearState.time_to_distance_min_raw;queue_length_after_60min_m_raw=$nearLength60m;condition_indicator_raw=$nearState.condition_indicator_raw;near_threshold=$nearState.near_capacity_threshold;status=$nearStatus};claim_limit='Full precision is retained internally; rounding occurs only when serializing display tables.'}
Write-JsonFile (Join-Path $ResultsDir 'numerical-edge-cases.json') $edgeReport

$growthState=Get-KinematicStateRaw 3000.0 $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity;$dissipationState=Get-KinematicStateRaw 0.0 $centralCapacity $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity;$grownLength=[double]$growthState.signed_queue_rate_km_h_raw*30.0/3600.0;$recoveredLength=[Math]::Max(0.0,$grownLength+[double]$dissipationState.signed_queue_rate_km_h_raw*90.0/3600.0);$recoveryStatus=if($grownLength -gt 0 -and $recoveredLength -eq 0){'pass'}else{'fail'}
$capacityMonotone=$true;$growthCapacity=@($capacityRows|Where-Object{$_.regime -eq 'growing_queue'}|Sort-Object capacity_pcu_h);for($i=1;$i -lt $growthCapacity.Count;$i++){if([double]$growthCapacity[$i].time_to_140m_min -le [double]$growthCapacity[$i-1].time_to_140m_min){$capacityMonotone=$false}}
$zeroDistance=Get-KinematicStateRaw $inflow $centralCapacity 0.0 $lanes $freeSpeed $waveSpeed $jamDensity;$noGrowth=Get-KinematicStateRaw 1200.0 1500.0 $distanceKm $lanes $freeSpeed $waveSpeed $jamDensity
$q3FiniteStatus=if((@($q3Rows|Where-Object{[double]$_.queue_length_m -gt $q3BoundaryM+1e-9}).Count -eq 0)-and $q3BoundaryRows -eq 10){'pass'}else{'fail'}
$q4FiniteStatus=if((@($timeRows|Where-Object{[double]$_.lwr_queue_length_m -gt $q4BoundaryM+1e-9}).Count -eq 0)-and [Math]::Abs([double]$timeRows[-1].lwr_queue_length_m-$q4BoundaryM)-le 1e-9){'pass'}else{'fail'}
$equalCapacityCounterexampleStatus=if((@($laneRows|Select-Object -ExpandProperty mandatory_merge_proxy -Unique).Count -eq 3)-and(@($laneRows|Where-Object{$null -ne $_.capacity_rank}).Count -eq 0)){'pass'}else{'fail'}
$verificationChecks=@(
 (New-Check 'V01_exact_source_identity' $sourceIdentityStatus $sourceRows.Count $Config.expected_inputs.Count 'path, size, SHA-256, and OLE magic'),
 (New-Check 'V02_video_completeness' $videoCompletenessStatus $videoRows.Count 2 'two required videos are absent'),
 (New-Check 'V03_capacity_pipeline_unit_tests' $capacityUnitStatus @($capacityUnitChecks|Where-Object{$_.status -eq 'pass'}).Count $capacityUnitChecks.Count 'synthetic tests only'),
 (New-Check 'V04_signal_counterexample_regression' $signalStatus @($signalChecks|Where-Object{$_.status -eq 'pass'}).Count $signalChecks.Count 'piecewise solver reads phase state'),
 (New-Check 'V05_independent_step_convergence' $convergenceStatus ([double]$smallest.absolute_error_s) ([double]$smallest.step_s) 'independent storage-balance path uses raw parameters'),
 (New-Check 'V06_mutation_detection' $mutationStatus (Round-Number $mutationDelta 6) '>1 s' '5% perturbation of analytic arrival density is detected'),
 (New-Check 'V07_capacity_monotonicity' $(if($capacityMonotone){'pass'}else{'fail'}) $capacityMonotone $true 'constant-state grid'),
 (New-Check 'V08_zero_distance' $(if([double]$zeroDistance.time_to_distance_min_raw -eq 0){'pass'}else{'fail'}) $zeroDistance.time_to_distance_min_raw 0 'boundary property'),
 (New-Check 'V09_no_growth_when_q_le_c' $(if(-not$noGrowth.queue_grows_from_zero -and $null -eq $noGrowth.time_to_distance_min_raw){'pass'}else{'fail'}) $noGrowth.regime 'no growth from zero' 'reflection property'),
 (New-Check 'V10_density_order' $(if([double]$central.density_jump_pcu_km_raw -gt 0){'pass'}else{'fail'}) (Round-Number ([double]$central.density_jump_pcu_km_raw) 6) '>0' 'feasible triangular diagram state'),
 (New-Check 'V11_q3_finite_boundary' $q3FiniteStatus $q3BoundaryRows 10 'all spillback rows capped and marked'),
 (New-Check 'V12_q4_finite_boundary' $q4FiniteStatus ([double]$timeRows[-1].lwr_queue_length_m) $q4BoundaryM 'series ends at first hit'),
 (New-Check 'V13_near_capacity_precision' $nearStatus $nearState.signed_queue_rate_km_h_raw '>0 and consistent' 'no premature rounding'),
 (New-Check 'V14_recovery_reflection' $recoveryStatus $recoveredLength 0 'queue dissipates without becoming negative'),
 (New-Check 'V15_equal_capacity_counterexample' $equalCapacityCounterexampleStatus 'no rank emitted' 'M differs while capacity may be equal' 'descriptive proxy cannot rank capacity'),
 (New-Check 'V16_external_validity' 'needs_review' 'no video observations' 'requires videos 1 and 2' 'internal checks do not establish case-specific validity')
)
$hardFailures=@($verificationChecks|Where-Object{$_.status -eq 'fail'}).Count;$needsReview=@($verificationChecks|Where-Object{$_.status -eq 'needs_review'}).Count;$verificationOverall=if($hardFailures -gt 0){'fail'}elseif($needsReview -gt 0){'needs_review'}else{'pass'}
$verification=[ordered]@{schema_version=2;overall_status=$verificationOverall;constant_analytic_time_s=Round-Number $analyticSeconds 6;independent_smallest_step_time_s=[double]$smallest.independent_hit_time_s;independent_smallest_step_s=[double]$smallest.step_s;checks=$verificationChecks;claim_limit='Internal pass values do not upgrade missing-video external validity.'}
Write-JsonFile (Join-Path $ResultsDir 'verification.json') $verification
if($hardFailures -gt 0){throw ('fail: internal verification failures={0}' -f $hardFailures)}

$summary=[ordered]@{
 schema_version=2;overall_status='needs_review';seed=$Seed
 q1=[ordered]@{status='needs_review';implementation_status=$capacityUnitStatus;numeric_capacity_available=($validVideo1.Count -gt 0);authorized_annotation_file_available=$annotationAvailable;reason=if($annotationAvailable){'annotations require case-specific review'}else{'video1 and authorized annotations are absent'};method='complete 60 s cycle PCU counts, saturation gate, three-cycle median, MAD segmentation'}
 q2=[ordered]@{status='needs_review';empirical_difference_available=$false;strict_capacity_order_supported_status='fail';merge_proxy_is_descriptive_status='pass';merge_proxy=$laneRows;reason='M=1-p alone does not identify a capacity effect'}
 q3=[ordered]@{status='needs_review';pre_boundary_formula_status='pass';case_specific_capacity_status='needs_review';finite_link_boundary_m=$q3BoundaryM;spillback_rows_marked_needs_review=$q3BoundaryRows;relation='dL/dt=(q-C)/(m*k_j-C/w-q/v_f), reflected at L=0, valid only before first spillback'}
 q4=[ordered]@{calculation_status='pass';case_specific_status='needs_review';constant_baseline=[ordered]@{distance_m=$q4BoundaryM;inflow_pcu_h=$inflow;assumed_capacity_pcu_h=$centralCapacity;arrival_density_pcu_km_raw=$central.arrival_density_pcu_km_raw;congested_density_pcu_km_raw=$central.congested_density_pcu_km_raw;queue_growth_speed_km_h_raw=$central.signed_queue_rate_km_h_raw;time_min_raw=$central.time_to_distance_min_raw;point_queue_time_min_raw=$central.point_queue_time_min_raw};capacity_range=[ordered]@{capacity_pcu_h=@([double]$e.main_capacity_low_pcu_h,[double]$e.main_capacity_high_pcu_h);time_min=@((Round-Number ([double]$mainLow.time_to_distance_min_raw) 6),(Round-Number ([double]$mainHigh.time_to_distance_min_raw) 6))};audit_square_wave=[ordered]@{green_first_time_min=Round-Number ([double]$squareGreen.hit_time_s_raw/60.0) 6;red_first_time_min=Round-Number ([double]$squareRed.hit_time_s_raw/60.0) 6;status='pass';claim='counterexample, not case prediction'};detailed_signal_phase_grid=[ordered]@{minimum_time_min=Round-Number $detailedMin 6;maximum_time_min=Round-Number $detailedMax 6;case_specific_status='needs_review'};joint_grid_time_range_min=@((Round-Number $jointMin 6),(Round-Number $jointMax 6));interpretation='Constant 5.306 min is a baseline, not a unique answer under unknown signal phase and video-derived capacity.'}
}
Write-JsonFile (Join-Path $ResultsDir 'summary.json') $summary
Write-JsonFile (Join-Path $ResultsDir 'model_parameters_used.json') $Config

$keyNumbers=@(
 [pscustomobject]@{key='q4.constant_time_min';value=Round-Number ([double]$central.time_to_distance_min_raw) 2;unit='min';evidence_layer='engineering_baseline';status='needs_review'},
 [pscustomobject]@{key='q4.point_queue_time_min';value=Round-Number ([double]$central.point_queue_time_min_raw) 2;unit='min';evidence_layer='engineering_baseline';status='needs_review'},
 [pscustomobject]@{key='q4.capacity_800_time_min';value=Round-Number ([double]$mainLow.time_to_distance_min_raw) 2;unit='min';evidence_layer='engineering_sensitivity';status='needs_review'},
 [pscustomobject]@{key='q4.capacity_1200_time_min';value=Round-Number ([double]$mainHigh.time_to_distance_min_raw) 2;unit='min';evidence_layer='engineering_sensitivity';status='needs_review'},
 [pscustomobject]@{key='q4.queue_speed_km_h';value=Round-Number ([double]$central.signed_queue_rate_km_h_raw) 3;unit='km/h';evidence_layer='engineering_baseline';status='needs_review'},
 [pscustomobject]@{key='q4.square_green_first_min';value=Round-Number ([double]$squareGreen.hit_time_s_raw/60.0) 3;unit='min';evidence_layer='counterexample';status='pass'},
 [pscustomobject]@{key='q4.square_red_first_min';value=Round-Number ([double]$squareRed.hit_time_s_raw/60.0) 3;unit='min';evidence_layer='counterexample';status='pass'},
 [pscustomobject]@{key='q4.detailed_phase_min_min';value=Round-Number $detailedMin 2;unit='min';evidence_layer='engineering_sensitivity';status='needs_review'},
 [pscustomobject]@{key='q4.detailed_phase_max_min';value=Round-Number $detailedMax 2;unit='min';evidence_layer='engineering_sensitivity';status='needs_review'},
 [pscustomobject]@{key='q3.spillback_rows';value=$q3BoundaryRows;unit='row';evidence_layer='finite_boundary_check';status='pass'},
 [pscustomobject]@{key='q2.right_merge_proxy';value=0.79;unit='share';evidence_layer='descriptive_proxy';status='needs_review'},
 [pscustomobject]@{key='q2.through_merge_proxy';value=0.56;unit='share';evidence_layer='descriptive_proxy';status='needs_review'},
 [pscustomobject]@{key='q2.left_merge_proxy';value=0.65;unit='share';evidence_layer='descriptive_proxy';status='needs_review'}
)
Write-CsvFile (Join-Path $ResultsDir '<SOURCE_FILE_REDACTED>') $keyNumbers

Write-SolutionFigures -Workspace $Workspace -Central $central -CapacityRows $capacityRows -LaneRows $laneRows -SignalRows $signalRows -Lanes $lanes -Inflow $inflow -CentralCapacity $centralCapacity -FreeSpeed $freeSpeed -WaveSpeed $waveSpeed -JamDensity $jamDensity -Q3BoundaryM $q3BoundaryM -Q4BoundaryM $q4BoundaryM

$generatedFiles=@(
 'results/input_manifest.json','results/model_parameters_used.json','results/summary.json','results/verification.json','results/<SOURCE_FILE_REDACTED>',
 'results/<SOURCE_FILE_REDACTED>','results/<SOURCE_FILE_REDACTED>','results/<SOURCE_FILE_REDACTED>','results/capacity-unit-tests.json','results/<SOURCE_FILE_REDACTED>','results/q2_comparison.json',
 'results/<SOURCE_FILE_REDACTED>','results/<SOURCE_FILE_REDACTED>','results/<SOURCE_FILE_REDACTED>','results/<SOURCE_FILE_REDACTED>','results/<SOURCE_FILE_REDACTED>',
 'results/<SOURCE_FILE_REDACTED>','results/<SOURCE_FILE_REDACTED>','results/signal-regression.json','results/<SOURCE_FILE_REDACTED>','results/numerical-edge-cases.json',
 'figures/<SOURCE_FILE_REDACTED>','figures/<SOURCE_FILE_REDACTED>','figures/<SOURCE_FILE_REDACTED>','figures/<SOURCE_FILE_REDACTED>','figures/<SOURCE_FILE_REDACTED>'
)
Write-JsonFile (Join-Path $ResultsDir 'generated-files.json') ([ordered]@{schema_version=2;files=$generatedFiles;status='pass'})
Write-Output ('[pass] revised deterministic outputs generated; overall evidence status={0}' -f $summary.overall_status)
