param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$Workspace = [System.IO.Path]::GetFullPath($Workspace)
$KeyPath = Join-Path $Workspace 'results\<SOURCE_FILE_REDACTED>'
$MarkdownPath = Join-Path $Workspace 'paper\paper.md'
$TexPath = Join-Path $Workspace 'paper\main.tex'
$OutputPath = Join-Path $Workspace 'results\paper_consistency.json'
foreach($path in @($KeyPath,$MarkdownPath,$TexPath)){if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Missing consistency input: $path"}}

$markdown=Get-Content -Raw -LiteralPath $MarkdownPath
$tex=Get-Content -Raw -LiteralPath $TexPath
$markdownVisible=[regex]::Replace($markdown,'(?s)<!--.*?-->','')
$texVisible=[regex]::Replace($tex,'(?m)^\s*%.*$','')
$keyRows=@(Import-Csv -LiteralPath $KeyPath)
$checks=@()
function Add-Check([string]$Id,[bool]$Condition,$Metric,$Expected,[string]$Claim){
    $script:checks += [pscustomobject]@{id=$Id;status=if($Condition){'pass'}else{'fail'};metric=$Metric;expected=$Expected;claim=$Claim}
}

foreach($row in $keyRows){
    $mdToken='<!-- RESULT:{0}={1} -->' -f $row.key,$row.value
    $texToken='% RESULT:{0}={1}' -f $row.key,$row.value
    Add-Check ('marker:'+ $row.key) ($markdown.Contains($mdToken)-and$tex.Contains($texToken)) 'both_sources' 'both_sources' '<SOURCE_FILE_REDACTED>'
    $valueText=[string]$row.value
    Add-Check ('visible_value:'+ $row.key) ($markdownVisible.Contains($valueText)-and$texVisible.Contains($valueText)) $valueText $valueText 'value appears outside comments in both sources'
}

$figureNames=@('<SOURCE_FILE_REDACTED>','<SOURCE_FILE_REDACTED>','<SOURCE_FILE_REDACTED>','<SOURCE_FILE_REDACTED>','<SOURCE_FILE_REDACTED>')
foreach($name in $figureNames){
    $exists=Test-Path -LiteralPath (Join-Path $Workspace ('figures\'+$name)) -PathType Leaf
    $mdRef=$markdownVisible.Contains('../figures/'+$name);$texRef=$texVisible.Contains($name)
    Add-Check ('figure:'+ $name) ($exists-and$mdRef-and$texRef) "$exists/$mdRef/$texRef" 'True/True/True' 'file exists and is visibly referenced'
}
for($q=1;$q -le 4;$q++){
    $pattern='问题\s*{0}' -f $q
    Add-Check ('question_coverage:'+ $q) ($markdownVisible-match$pattern-and$texVisible-match$pattern) 'both_sources' 'both_sources' 'paper visibly addresses each question'
}

Add-Check 'formula:seconds_capacity' ($markdownVisible.Contains('3600P_j')-and$texVisible.Contains('3600P_j')-and$markdownVisible.Contains('\Delta t_{j,s}')-and$texVisible.Contains('\Delta t_{j,s}')) 'seconds_formula' 'seconds_formula' 'capacity uses seconds exactly once'
Add-Check 'formula:event_reflection' ($markdownVisible.Contains('\max\{0,L^-+g(q,C)\Delta t\}')-and$texVisible.Contains('\max\{0,L^-+g(q,C)\Delta t\}')) 'reflection_formula' 'reflection_formula' 'time-varying queue is reflected at zero'
Add-Check 'claim:finite_boundaries' ($markdownVisible.Contains('240 m')-and$markdownVisible.Contains('140 m')-and$texVisible.Contains('240 m')-and$texVisible.Contains('140 m')) '240/140' '240/140' 'both finite boundaries are visible'
Add-Check 'claim:signal_nonuniqueness' ($markdownVisible.Contains('3.260 min')-and$markdownVisible.Contains('3.760 min')-and$texVisible.Contains('3.260 min')-and$texVisible.Contains('3.760 min')) '3.260/3.760' '3.260/3.760' 'same-mean phase counterexample is visible'
Add-Check 'claim:q2_no_rank' ($markdownVisible.Contains('不能识别严格容量排序')-and$texVisible.Contains('不能识别严格容量排序')) 'no_rank' 'no_rank' 'descriptive proxy does not identify capacity order'
Add-Check 'claim:independent_validation' ($markdownVisible.Contains('不读取解析程序返回的速度')-and$texVisible.Contains('不读取解析程序返回的速度')) 'independent_path' 'independent_path' 'validation independence is disclosed'
Add-Check 'forbidden:post_boundary_value' (-not$markdownVisible.Contains('145.118692')-and-not$texVisible.Contains('145.118692')) 'absent' 'absent' 'invalid post-boundary value is not reported'
Add-Check 'forbidden:unsupported_rank_phrase' (-not$markdownVisible.Contains('预测容量排序')-and-not$texVisible.Contains('预测容量排序')) 'absent' 'absent' 'removed unsupported causal ranking'
Add-Check 'typography:qquad' (-not[regex]::IsMatch($markdownVisible,'(?<!\\)qquad')-and-not[regex]::IsMatch($texVisible,'(?<!\\)qquad')) 'no_unescaped_qquad' 'no_unescaped_qquad' 'qquad has a backslash'

$braceOpen=([regex]::Matches($texVisible,'(?<!\\)\{')).Count
$braceClose=([regex]::Matches($texVisible,'(?<!\\)\}')).Count
Add-Check 'tex:brace_balance' ($braceOpen-eq$braceClose) "$braceOpen/$braceClose" 'equal' 'static brace balance'
$begins=@([regex]::Matches($texVisible,'\\begin\{([^}]+)\}')|ForEach-Object{$_.Groups[1].Value}|Group-Object|ForEach-Object{"$($_.Name):$($_.Count)"}|Sort-Object)
$ends=@([regex]::Matches($texVisible,'\\end\{([^}]+)\}')|ForEach-Object{$_.Groups[1].Value}|Group-Object|ForEach-Object{"$($_.Name):$($_.Count)"}|Sort-Object)
Add-Check 'tex:environment_balance' (($begins-join'|')-eq($ends-join'|')) ($begins-join'|') ($ends-join'|') 'static environment balance'

$failures=@($checks|Where-Object{$_.status-eq'fail'}).Count
$engines=@('xelatex','latexmk','tectonic'|ForEach-Object{Get-Command $_ -ErrorAction SilentlyContinue}|Where-Object{$null-ne$_}|ForEach-Object{$_.Name})
$report=[ordered]@{
    schema_version=2
    overall_status=if($failures-eq0){'pass'}else{'fail'}
    key_number_count=$keyRows.Count
    check_count=$checks.Count
    failure_count=$failures
    visible_claim_check_status=if(@($checks|Where-Object{$_.id-like'visible_value:*'-and$_.status-eq'fail'}).Count-eq0){'pass'}else{'fail'}
    typography_status=if(@($checks|Where-Object{$_.id-like'typography:*'-and$_.status-eq'fail'}).Count-eq0){'pass'}else{'fail'}
    tex_static_status=if(@($checks|Where-Object{$_.id-like'tex:*'-and$_.status-eq'fail'}).Count-eq0){'pass'}else{'fail'}
    pdf_compile_status='needs_review'
    pdf_visual_inspection_status='needs_review'
    tex_engines_found=$engines
    checks=$checks
    claim_limit='This checks visible claims, formulas, boundaries, figures, and static TeX structure; it does not prove mathematical or external validity.'
}
[System.IO.File]::WriteAllText($OutputPath,($report|ConvertTo-Json -Depth 15)+"`n",$Utf8NoBom)
if($failures-gt0){throw ('fail: paper consistency failures={0}' -f $failures)}
Write-Output ('[pass] paper/result visible consistency checks={0}; pdf status=needs_review' -f $checks.Count)
