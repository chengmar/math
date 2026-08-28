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

foreach ($path in @($KeyPath, $MarkdownPath, $TexPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing consistency input: $path" }
}

$markdown = Get-Content -Raw -LiteralPath $MarkdownPath
$tex = Get-Content -Raw -LiteralPath $TexPath
$keyRows = @(Import-Csv -LiteralPath $KeyPath)
$checks = @()
foreach ($row in $keyRows) {
    $mdToken = '<!-- RESULT:{0}={1} -->' -f $row.key, $row.value
    $texToken = '% RESULT:{0}={1}' -f $row.key, $row.value
    $mdFound = $markdown.Contains($mdToken)
    $texFound = $tex.Contains($texToken)
    $checks += [pscustomobject]@{
        id = 'number_marker:' + $row.key
        markdown_found = $mdFound
        tex_found = $texFound
        status = if ($mdFound -and $texFound) { 'pass' } else { 'fail' }
    }
}

foreach ($name in @('<SOURCE_FILE_REDACTED>','<SOURCE_FILE_REDACTED>','<SOURCE_FILE_REDACTED>','<SOURCE_FILE_REDACTED>')) {
    $exists = Test-Path -LiteralPath (Join-Path $Workspace ('figures\' + $name)) -PathType Leaf
    $mdReferenced = $markdown.Contains('../figures/' + $name)
    $texReferenced = $tex.Contains($name)
    $checks += [pscustomobject]@{
        id = 'figure:' + $name
        file_exists = $exists
        markdown_referenced = $mdReferenced
        tex_referenced = $texReferenced
        status = if ($exists -and $mdReferenced -and $texReferenced) { 'pass' } else { 'fail' }
    }
}

for ($q = 1; $q -le 4; $q++) {
    $pattern = '问题\s*{0}' -f $q
    $mdCovered = $markdown -match $pattern
    $texCovered = $tex -match $pattern
    $checks += [pscustomobject]@{
        id = 'question_coverage:' + $q
        markdown_found = $mdCovered
        tex_found = $texCovered
        status = if ($mdCovered -and $texCovered) { 'pass' } else { 'fail' }
    }
}

$failures = @($checks | Where-Object { $_.status -eq 'fail' }).Count
$report = [ordered]@{
    schema_version = 1
    overall_status = if ($failures -eq 0) { 'pass' } else { 'fail' }
    key_number_count = $keyRows.Count
    check_count = $checks.Count
    failure_count = $failures
    checks = $checks
    claim_limit = 'This checks declared markers and figure references, not mathematical correctness.'
}
$json = $report | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($OutputPath, $json + "`n", $Utf8NoBom)
if ($failures -gt 0) { throw ('[FAIL] paper consistency failures={0}' -f $failures) }
Write-Output ('[PASS] paper/result consistency checks={0}' -f $checks.Count)
