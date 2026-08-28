param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$Workspace = [System.IO.Path]::GetFullPath($Workspace)
$SolveScript = Join-Path $PSScriptRoot 'solve.ps1'
$GeneratedManifest = Join-Path $Workspace 'results\generated-files.json'
$OutputPath = Join-Path $Workspace 'results\reproducibility-check.json'

function Get-Snapshot {
    param([string[]]$RelativePaths)
    $rows = @()
    foreach ($relative in $RelativePaths) {
        $absolute = Join-Path $Workspace $relative
        if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) { throw "Missing generated output: $relative" }
        $item = Get-Item -LiteralPath $absolute
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $absolute
        $rows += [pscustomobject]@{ path=$relative; bytes=[long]$item.Length; sha256=$hash.Hash.ToLowerInvariant() }
    }
    return $rows
}

& $SolveScript -Workspace $Workspace | Out-Null
$manifest = Get-Content -Raw -LiteralPath $GeneratedManifest | ConvertFrom-Json
$tracked = @($manifest.files) + @('results/generated-files.json')
$first = @(Get-Snapshot $tracked)
& $SolveScript -Workspace $Workspace | Out-Null
$second = @(Get-Snapshot $tracked)

$checks = @()
for ($i=0; $i -lt $tracked.Count; $i++) {
    $same = ($first[$i].sha256 -eq $second[$i].sha256) -and ($first[$i].bytes -eq $second[$i].bytes)
    $checks += [pscustomobject]@{
        path = $tracked[$i]
        first_sha256 = $first[$i].sha256
        second_sha256 = $second[$i].sha256
        bytes = $second[$i].bytes
        status = if ($same) { 'pass' } else { 'fail' }
    }
}
$failures = @($checks | Where-Object { $_.status -eq 'fail' }).Count
$report = [ordered]@{
    schema_version = 1
    overall_status = if ($failures -eq 0) { 'pass' } else { 'fail' }
    runs_compared = 2
    tracked_file_count = $tracked.Count
    failure_count = $failures
    checks = $checks
    claim_limit = 'Hash equality proves deterministic reproduction of declared files only.'
}
$json = $report | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($OutputPath, $json + "`n", $Utf8NoBom)
if ($failures -gt 0) { throw ('[FAIL] reproducibility mismatches={0}' -f $failures) }
Write-Output ('[PASS] two-run hashes matched for {0} files' -f $tracked.Count)
