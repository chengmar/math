$ErrorActionPreference = 'Stop'
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$paperDir = Join-Path $root 'paper'
$resultsDir = Join-Path $root 'results'
$figureDir = Join-Path $root 'figures'
$markdownPath = Join-Path $paperDir 'paper.md'
$texPath = Join-Path $paperDir 'main.tex'
$macroPath = Join-Path $paperDir 'generated-values.tex'
$keyPath = Join-Path $resultsDir 'key_results.json'

$markdown = Get-Content -LiteralPath $markdownPath -Raw
$tex = Get-Content -LiteralPath $texPath -Raw
$macroText = Get-Content -LiteralPath $macroPath -Raw
$key = Get-Content -LiteralPath $keyPath -Raw | ConvertFrom-Json
$checks = @()

function Add-Check {
    param([string]$Id, [string]$Status, [string]$Detail)
    $script:checks += [pscustomobject]@{ check_id = $Id; status = $Status; detail = $Detail }
}

$expectedMarkdownTokens = @(
    ('{0:F3}' -f [double]$key.q1.length_at_0900_m),
    ('{0:F3}' -f [double]$key.q1.minimum_length_m),
    ('{0:F3}' -f [double]$key.q1.length_at_1500_m),
    [string]$key.q1.solar_noon_beijing,
    ('{0:F1}' -f [double]$key.q2.primary_rotation.latitude_deg),
    ('{0:F1}' -f [double]$key.q2.primary_rotation.longitude_deg),
    ('{0:F2}' -f [double]$key.q2.primary_rotation.inferred_height_m)
)
foreach ($candidate in @($key.q3_attachment2_rotation_candidates + $key.q3_attachment3_rotation_candidates)) {
    $expectedMarkdownTokens += ([datetime]$candidate.date).ToString('MM-dd')
    $expectedMarkdownTokens += ('{0:F1}' -f [double]$candidate.latitude_deg)
    $expectedMarkdownTokens += ('{0:F1}' -f [double]$candidate.longitude_deg)
}
foreach ($token in ($expectedMarkdownTokens | Sort-Object -Unique)) {
    Add-Check ("markdown_contains_$($token -replace '[^0-9A-Za-z]+','_')") `
        $(if ($markdown.Contains($token)) { 'pass' } else { 'fail' }) "expected token=$token"
}

$macroMap = [ordered]@{
    QOneNineLength = '{0:F3}' -f [double]$key.q1.length_at_0900_m
    QOneNoonTime = ([string]$key.q1.solar_noon_beijing).Substring(0, 5)
    QOneMinimumLength = '{0:F3}' -f [double]$key.q1.minimum_length_m
    QOneFifteenLength = '{0:F3}' -f [double]$key.q1.length_at_1500_m
    QTwoLatitude = '{0:F1}' -f [double]$key.q2.primary_rotation.latitude_deg
    QTwoLongitude = '{0:F1}' -f [double]$key.q2.primary_rotation.longitude_deg
    QTwoHeight = '{0:F2}' -f [double]$key.q2.primary_rotation.inferred_height_m
    QTwoRmseMm = '{0:F3}' -f (1000.0 * [double]$key.q2.primary_rotation.tip_rmse_m)
    QThreeTwoDateA = ([datetime]$key.q3_attachment2_rotation_candidates[0].date).ToString('MM-dd')
    QThreeTwoLatA = '{0:F1}' -f [double]$key.q3_attachment2_rotation_candidates[0].latitude_deg
    QThreeTwoLonA = '{0:F1}' -f [double]$key.q3_attachment2_rotation_candidates[0].longitude_deg
    QThreeTwoDateB = ([datetime]$key.q3_attachment2_rotation_candidates[1].date).ToString('MM-dd')
    QThreeTwoLatB = '{0:F1}' -f [double]$key.q3_attachment2_rotation_candidates[1].latitude_deg
    QThreeTwoLonB = '{0:F1}' -f [double]$key.q3_attachment2_rotation_candidates[1].longitude_deg
    QThreeThreeDateA = ([datetime]$key.q3_attachment3_rotation_candidates[0].date).ToString('MM-dd')
    QThreeThreeLatA = '{0:F1}' -f [double]$key.q3_attachment3_rotation_candidates[0].latitude_deg
    QThreeThreeLonA = '{0:F1}' -f [double]$key.q3_attachment3_rotation_candidates[0].longitude_deg
    QThreeThreeDateB = ([datetime]$key.q3_attachment3_rotation_candidates[1].date).ToString('MM-dd')
    QThreeThreeLatB = '{0:F1}' -f [double]$key.q3_attachment3_rotation_candidates[1].latitude_deg
    QThreeThreeLonB = '{0:F1}' -f [double]$key.q3_attachment3_rotation_candidates[1].longitude_deg
}
foreach ($name in $macroMap.Keys) {
    $expectedLine = "\newcommand{\$name}{$($macroMap[$name])}"
    Add-Check "macro_value_$name" $(if ($macroText.Contains($expectedLine)) { 'pass' } else { 'fail' }) $expectedLine
    Add-Check "macro_used_$name" $(if ($tex.Contains("\$name")) { 'pass' } else { 'fail' }) "macro must be used in main.tex"
}

$markdownImages = [regex]::Matches($markdown, '!\[[^\]]*\]\(([^)]+)\)')
foreach ($match in $markdownImages) {
    $relative = $match.Groups[1].Value
    $resolved = [IO.Path]::GetFullPath((Join-Path $paperDir $relative))
    Add-Check ("markdown_figure_" + [IO.Path]::GetFileNameWithoutExtension($resolved)) `
        $(if (Test-Path -LiteralPath $resolved) { 'pass' } else { 'fail' }) $relative
}

$texImages = [regex]::Matches($tex, '\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}')
foreach ($match in $texImages) {
    $fileName = $match.Groups[1].Value
    $resolved = Join-Path $figureDir $fileName
    Add-Check ("tex_figure_" + [IO.Path]::GetFileNameWithoutExtension($fileName)) `
        $(if (Test-Path -LiteralPath $resolved) { 'pass' } else { 'fail' }) $fileName
}

$leftBraces = ([regex]::Matches($tex, '(?<!\\)\{')).Count
$rightBraces = ([regex]::Matches($tex, '(?<!\\)\}')).Count
Add-Check 'tex_unescaped_brace_count' $(if ($leftBraces -eq $rightBraces) { 'pass' } else { 'fail' }) "left=$leftBraces right=$rightBraces"

$beginEnvironments = [regex]::Matches($tex, '\\begin\{([^}]+)\}') | ForEach-Object { $_.Groups[1].Value }
$endEnvironments = [regex]::Matches($tex, '\\end\{([^}]+)\}') | ForEach-Object { $_.Groups[1].Value }
$environmentNames = @($beginEnvironments + $endEnvironments | Sort-Object -Unique)
foreach ($environment in $environmentNames) {
    $beginCount = @($beginEnvironments | Where-Object { $_ -eq $environment }).Count
    $endCount = @($endEnvironments | Where-Object { $_ -eq $environment }).Count
    Add-Check "tex_environment_$environment" $(if ($beginCount -eq $endCount) { 'pass' } else { 'fail' }) "begin=$beginCount end=$endCount"
}

foreach ($document in @(
    [pscustomobject]@{ Name = 'markdown'; Text = $markdown },
    [pscustomobject]@{ Name = 'tex'; Text = $tex })) {
    $tabCount = ([regex]::Matches($document.Text, "`t")).Count
    $nulCount = ([regex]::Matches($document.Text, "`0")).Count
    $bareCrCount = ([regex]::Matches($document.Text, "`r(?!`n)")).Count
    Add-Check "$($document.Name)_forbidden_control_characters" `
        $(if ($tabCount -eq 0 -and $nulCount -eq 0 -and $bareCrCount -eq 0) { 'pass' } else { 'fail' }) `
        "tab=$tabCount nul=$nulCount bare_cr=$bareCrCount"
}

$q3CandidateFiles = @('<SOURCE_FILE_REDACTED>','<SOURCE_FILE_REDACTED>')
foreach ($file in $q3CandidateFiles) {
    $headers = @((Import-Csv -LiteralPath (Join-Path $resultsDir $file) | Select-Object -First 1).PSObject.Properties.Name)
    Add-Check ("candidate_rank_semantics_" + [IO.Path]::GetFileNameWithoutExtension($file)) `
        $(if ('branch_rank' -in $headers -and 'global_rmse_rank' -in $headers -and 'rank' -notin $headers) { 'pass' } else { 'fail' }) `
        ($headers -join ',')
}

Add-Check 'q4_pixel_to_ground_formula_snapshot' `
    $(if ($markdown.Contains('G_pg') -and $tex.Contains('G_{\mathrm{pg}}') -and $markdown.Contains('pi(G_pg p_i)') -and $tex.Contains('\pi(G_{\mathrm{pg}}p_i)')) { 'pass' } else { 'fail' }) `
    'Both documents must state the fixed pixel-to-ground mapping with homogeneous normalization.'

$independentRows = @(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>'))
$passCount = @($independentRows | Where-Object status -eq 'pass').Count
$needsReviewCount = @($independentRows | Where-Object status -eq 'needs_review').Count
$failCount = @($independentRows | Where-Object status -eq 'fail').Count
$statusToken = "$passCount pass、$needsReviewCount needs_review、$failCount fail"
Add-Check 'independent_validation_status_counts_in_paper' `
    $(if ($markdown.Contains($statusToken) -and $tex.Contains($statusToken)) { 'pass' } else { 'fail' }) `
    $statusToken

foreach ($required in @('问题重述', '模型假设', '太阳影子正向模型', '逆模型', '结果', '验证', '局限', '结论', '复现')) {
    Add-Check ("required_topic_" + $required) `
        $(if ($markdown.Contains($required) -and $tex.Contains($required)) { 'pass' } else { 'fail' }) $required
}

$placeholderPattern = '题目名称（按匿名竞赛规则填写）|请替换|关键词一|pending'
Add-Check 'no_template_placeholder_markers' `
    $(if ($markdown -notmatch $placeholderPattern -and $tex -notmatch $placeholderPattern) { 'pass' } else { 'fail' }) $placeholderPattern

$latexCommands = @('xelatex', 'latexmk', 'pdflatex') | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue }
Add-Check 'latex_compilation' $(if ($latexCommands.Count -gt 0) { 'needs_review' } else { 'needs_review' }) `
    $(if ($latexCommands.Count -gt 0) { 'Engine detected but compilation is a separate check.' } else { 'No LaTeX engine detected; no compilation pass claimed.' })

$markdownHash = (Get-FileHash -LiteralPath $markdownPath -Algorithm SHA256).Hash.ToLowerInvariant()
$texHash = (Get-FileHash -LiteralPath $texPath -Algorithm SHA256).Hash.ToLowerInvariant()
$keyHash = (Get-FileHash -LiteralPath $keyPath -Algorithm SHA256).Hash.ToLowerInvariant()
$boundChecks = foreach ($check in $checks) {
    $check | Add-Member -NotePropertyName paper_md_sha256 -NotePropertyValue $markdownHash -PassThru |
        Add-Member -NotePropertyName paper_tex_sha256 -NotePropertyValue $texHash -PassThru |
        Add-Member -NotePropertyName key_results_sha256 -NotePropertyValue $keyHash -PassThru
}
$boundChecks | Export-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>') -NoTypeInformation -Encoding utf8
$failureCount = @($checks | Where-Object status -eq 'fail').Count
if ($failureCount -gt 0) {
    Write-Host "[FAIL] paper consistency found $failureCount failed checks"
    exit 1
}
Write-Host "[PASS] paper/result consistency checks completed; LaTeX compilation needs_review"
