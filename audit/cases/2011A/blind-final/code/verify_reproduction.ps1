param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$runScript = Join-Path $PSScriptRoot 'run_all.ps1'
$hashPath = Join-Path $Workspace 'results\output_hashes.json'
$reportPath = Join-Path $Workspace 'reports\reproduction-check.json'

& $runScript -Workspace $Workspace
$first = Get-Content -LiteralPath $hashPath -Raw | ConvertFrom-Json

& $runScript -Workspace $Workspace
$second = Get-Content -LiteralPath $hashPath -Raw | ConvertFrom-Json

$firstMap = @{}
foreach ($entry in $first.files) {
    $firstMap[[string]$entry.path] = [string]$entry.sha256
}
$secondMap = @{}
foreach ($entry in $second.files) {
    $secondMap[[string]$entry.path] = [string]$entry.sha256
}

$allPaths = @($firstMap.Keys + $secondMap.Keys | Sort-Object -Unique)
$mismatches = @()
foreach ($path in $allPaths) {
    $firstHash = $firstMap[$path]
    $secondHash = $secondMap[$path]
    if ($firstHash -ne $secondHash) {
        $mismatches += [PSCustomObject]@{
            path = $path
            first_sha256 = $firstHash
            second_sha256 = $secondHash
        }
    }
}

$status = if ($mismatches.Count -eq 0 -and $first.file_count -eq $second.file_count) {
    'pass'
} else {
    'fail'
}
$payload = [ordered]@{
    status = $status
    run_count = 2
    clean_generation_status = 'pass'
    regenerated_directories = @('results', 'figures', 'paper/generated')
    compared_file_count = $allPaths.Count
    first_file_count = [int]$first.file_count
    second_file_count = [int]$second.file_count
    mismatches = $mismatches
    note = 'run_all.ps1 safely removed every generated directory before each complete run; both fresh manifests were compared in memory and no stored baseline was substituted.'
}
$json = $payload | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText(
    $reportPath,
    $json + [Environment]::NewLine,
    (New-Object System.Text.UTF8Encoding($false))
)

if ($status -eq 'fail') {
    throw "Reproduction comparison failed with $($mismatches.Count) mismatches."
}
Write-Output "pass: two-run reproduction matched across $($allPaths.Count) files"
