param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot),
    [string]$Target = (Join-Path (Split-Path -Parent $PSScriptRoot) '_work\repro-run-clean-1')
)

$ErrorActionPreference = 'Stop'

function Resolve-WithinWorkspace {
    param([string]$Path)
    $root = [System.IO.Path]::GetFullPath($Workspace).TrimEnd('\')
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Target escapes workspace: $full"
    }
    return $full
}

$workspaceFull = [System.IO.Path]::GetFullPath($Workspace)
$targetFull = Resolve-WithinWorkspace $Target
if (Test-Path -LiteralPath $targetFull) {
    throw "Fresh reproducibility target already exists; choose a new -Target: $targetFull"
}

$directories = @(
    $targetFull,
    (Join-Path $targetFull 'code'),
    (Join-Path $targetFull 'input\problem'),
    (Join-Path $targetFull 'input\attachments')
)
foreach ($directory in $directories) {
    New-Item -ItemType Directory -Path $directory | Out-Null
}

$copies = @(
    @{
        Source = (Join-Path $workspaceFull 'code\convert_inputs.ps1')
        Destination = (Join-Path $targetFull 'code\convert_inputs.ps1')
    },
    @{
        Source = (Join-Path $workspaceFull 'code\solve_population.py')
        Destination = (Join-Path $targetFull 'code\solve_population.py')
    },
    @{
        Source = (Join-Path $workspaceFull 'input\problem\<SOURCE_FILE_REDACTED>')
        Destination = (Join-Path $targetFull 'input\problem\<SOURCE_FILE_REDACTED>')
    },
    @{
        Source = (Join-Path $workspaceFull 'input\attachments\<SOURCE_FILE_REDACTED>')
        Destination = (Join-Path $targetFull 'input\attachments\<SOURCE_FILE_REDACTED>')
    }
)
foreach ($copy in $copies) {
    if (-not (Test-Path -LiteralPath $copy.Source -PathType Leaf)) {
        throw "Required source missing: $($copy.Source)"
    }
    Copy-Item -LiteralPath $copy.Source -Destination $copy.Destination
}

$convertScript = Join-Path $targetFull 'code\convert_inputs.ps1'
& $convertScript -Workspace $targetFull | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Fresh input conversion failed: $LASTEXITCODE"
}

$solveScript = Join-Path $targetFull 'code\solve_population.py'
& python $solveScript --workspace $targetFull
if ($LASTEXITCODE -ne 0) {
    throw "Fresh numerical solve failed: $LASTEXITCODE"
}

$mainManifestPath = Join-Path $workspaceFull 'results\run_manifest.json'
$freshManifestPath = Join-Path $targetFull 'results\run_manifest.json'
$mainManifest = Get-Content -Raw -LiteralPath $mainManifestPath | ConvertFrom-Json
$freshManifest = Get-Content -Raw -LiteralPath $freshManifestPath | ConvertFrom-Json

$mainOutputs = @{}
foreach ($record in $mainManifest.outputs) {
    $mainOutputs[$record.relative_path] = $record.sha256
}
$freshOutputs = @{}
foreach ($record in $freshManifest.outputs) {
    $freshOutputs[$record.relative_path] = $record.sha256
}

$mismatches = @()
$allPaths = @($mainOutputs.Keys + $freshOutputs.Keys | Sort-Object -Unique)
foreach ($relativePath in $allPaths) {
    if (-not $mainOutputs.ContainsKey($relativePath)) {
        $mismatches += [ordered]@{ relative_path = $relativePath; problem = 'missing_in_main' }
    }
    elseif (-not $freshOutputs.ContainsKey($relativePath)) {
        $mismatches += [ordered]@{ relative_path = $relativePath; problem = 'missing_in_fresh' }
    }
    elseif ($mainOutputs[$relativePath] -ne $freshOutputs[$relativePath]) {
        $mismatches += [ordered]@{
            relative_path = $relativePath
            problem = 'sha256_mismatch'
            main_sha256 = $mainOutputs[$relativePath]
            fresh_sha256 = $freshOutputs[$relativePath]
        }
    }
}

$mainCsv = Join-Path $workspaceFull '_work\converted\<SOURCE_FILE_REDACTED>'
$freshCsv = Join-Path $targetFull '_work\converted\<SOURCE_FILE_REDACTED>'
$mainCsvHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $mainCsv).Hash.ToLowerInvariant()
$freshCsvHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $freshCsv).Hash.ToLowerInvariant()
$csvMatches = $mainCsvHash -eq $freshCsvHash
$passed = ($mismatches.Count -eq 0) -and $csvMatches -and ($freshManifest.judgment -eq 'pass')
$judgment = if ($passed) { 'pass' } else { 'fail' }

$relativeTarget = $targetFull.Substring($workspaceFull.TrimEnd('\').Length + 1).Replace('\', '/')
$report = [ordered]@{
    judgment = $judgment
    target = $relativeTarget
    conversion_csv = [ordered]@{
        judgment = if ($csvMatches) { 'pass' } else { 'fail' }
        main_sha256 = $mainCsvHash
        fresh_sha256 = $freshCsvHash
    }
    fresh_solver_judgment = $freshManifest.judgment
    deterministic_output_count = $allPaths.Count
    output_hash_judgment = if ($mismatches.Count -eq 0) { 'pass' } else { 'fail' }
    mismatches = $mismatches
}
$reportPath = Join-Path $workspaceFull 'results\reproducibility_check.json'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $reportPath,
    (($report | ConvertTo-Json -Depth 8) + "`n"),
    $utf8NoBom
)
$report | ConvertTo-Json -Depth 8
if (-not $passed) {
    exit 1
}
