param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($Workspace)
$failures = [Collections.Generic.List[string]]::new()
$calibration = Get-Content -LiteralPath (Join-Path $root 'results\calibration.json') -Raw | ConvertFrom-Json
$samples = Import-Csv -LiteralPath (Join-Path $root 'results\<SOURCE_FILE_REDACTED>')
$geometry2 = Get-Content -LiteralPath (Join-Path $root 'results\problem2-geometry.json') -Raw | ConvertFrom-Json
$geometry3 = Get-Content -LiteralPath (Join-Path $root 'results\problem3-geometry.json') -Raw | ConvertFrom-Json
$stability = Get-Content -LiteralPath (Join-Path $root 'results\stability.json') -Raw | ConvertFrom-Json
$sirt = Get-Content -LiteralPath (Join-Path $root 'results\sirt-iteration-curves.json') -Raw | ConvertFrom-Json
$generated = Get-Content -LiteralPath (Join-Path $root 'paper\generated-values.tex') -Raw
$markdown = Get-Content -LiteralPath (Join-Path $root 'paper\paper.md') -Raw
$tex = Get-Content -LiteralPath (Join-Path $root 'paper\main.tex') -Raw

function Assert-Contains {
    param([string]$Text, [string]$Expected, [string]$Label)
    if (-not $Text.Contains($Expected, [StringComparison]::Ordinal)) {
        $failures.Add("$Label lacks expected value: $Expected")
    }
}

function Assert-Macro {
    param([string]$Name, [string]$Value)
    Assert-Contains $generated "\newcommand{\$Name}{$Value}" "generated-values.tex"
}

Assert-Macro 'RotationCenterX' ([double]$calibration.rotation_center_mm.x).ToString('F4', [Globalization.CultureInfo]::InvariantCulture)
Assert-Macro 'RotationCenterY' ([double]$calibration.rotation_center_mm.y).ToString('F4', [Globalization.CultureInfo]::InvariantCulture)
Assert-Macro 'DetectorSpacing' ([double]$calibration.detector_spacing_mm).ToString('F4', [Globalization.CultureInfo]::InvariantCulture)
Assert-Macro 'DetectorNormalStart' ([double]$calibration.signed_detector_normal_start_degrees).ToString('F4', [Globalization.CultureInfo]::InvariantCulture)
Assert-Macro 'ProjectionGain' ([double]$calibration.gain).ToString('F4', [Globalization.CultureInfo]::InvariantCulture)

$words = @('One','Two','Three','Four','Five','Six','Seven','Eight','Nine','Ten')
for ($index = 0; $index -lt 10; $index++) {
    Assert-Macro "ProblemTwoPoint$($words[$index])" $samples[$index].problem2
    Assert-Macro "ProblemThreePoint$($words[$index])" $samples[$index].problem3
    Assert-Contains $markdown $samples[$index].problem2 "paper.md problem2 point $($index + 1)"
    Assert-Contains $markdown $samples[$index].problem3 "paper.md problem3 point $($index + 1)"
}
foreach ($summary in $sirt.summaries) {
    if ([int]$summary.selected_iterations -ne 80) {
        $failures.Add("Unexpected SIRT selection for $($summary.dataset)")
    }
}
Assert-Macro 'ProblemTwoIterations' '80'
Assert-Macro 'ProblemThreeIterations' '80'

$markdownValues = @(
    ([double]$calibration.rotation_center_mm.x).ToString('F4'),
    ([double]$calibration.rotation_center_mm.y).ToString('F4'),
    ([double]$calibration.detector_spacing_mm).ToString('F4'),
    ([double]$calibration.signed_detector_normal_start_degrees).ToString('F4'),
    ([double]$geometry2.envelope.center_x).ToString('F4'),
    ([double]$geometry2.envelope.center_y).ToString('F4'),
    ([double]$geometry3.envelope.center_x).ToString('F4'),
    ([double]$geometry3.envelope.center_y).ToString('F4'),
    (100 * [double]$geometry3.material_fraction).ToString('F4'),
    (100 * [double]$geometry3.reconstruction_threshold_porosity).ToString('F4')
)
foreach ($value in $markdownValues) { Assert-Contains $markdown $value 'paper.md primary results' }

$reductionMacros = @(
    @{ Name='Spacing'; Key='detector_spacing' },
    @{ Name='Start'; Key='start_angle' },
    @{ Name='CenterX'; Key='center_x' },
    @{ Name='CenterY'; Key='center_y' }
)
foreach ($item in $reductionMacros) {
    $interval = $stability.standard_deviation_reduction.($item.Key)
    Assert-Macro "$($item.Name)ReductionPercent" (100 * [double]$interval.estimate).ToString('F1')
    Assert-Macro "$($item.Name)ReductionLowerPercent" (100 * [double]$interval.lower95).ToString('F1')
    Assert-Macro "$($item.Name)ReductionUpperPercent" (100 * [double]$interval.upper95).ToString('F1')
}

foreach ($macro in @(
    'RotationCenterX','RotationCenterY','DetectorSpacing','DetectorNormalStart',
    'ProblemTwoPointOne','ProblemThreePointOne','SpacingReductionPercent')) {
    Assert-Contains $tex "\$macro" 'paper/main.tex'
}

$status = if ($failures.Count -eq 0) { 'pass' } else { 'fail' }
$report = [PSCustomObject]@{
    status = $status
    generated_macro_consistency = if ($failures.Count -eq 0) { 'pass' } else { 'fail' }
    markdown_primary_value_consistency = if ($failures.Count -eq 0) { 'pass' } else { 'fail' }
    tex_macro_usage = if ($failures.Count -eq 0) { 'pass' } else { 'fail' }
    sample_values_checked = 20
    selected_iteration_checks = 3
    failures = $failures
}
$report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath `
    (Join-Path $root 'results\paper-consistency.json') -Encoding utf8NoBOM
$report | ConvertTo-Json -Depth 5
if ($status -eq 'fail') { exit 1 }
