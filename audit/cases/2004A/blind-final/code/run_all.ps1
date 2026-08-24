param(
    [string]$Workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [int]$Bootstrap = 500,
    [int]$Seed = 2004
)

$ErrorActionPreference = 'Stop'
$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$phaseCheck = '<LAB_ROOT>\.agents\skills\cumcm-a-solve\scripts\check_phase.py'
$archive = Join-Path $workspacePath 'input\attachments\<SOURCE_FILE_REDACTED>'
$sourceDirectory = Join-Path $workspacePath 'work\source'
$stagingDirectory = Join-Path $workspacePath 'work\staging'
$resultDirectory = Join-Path $workspacePath 'results'
$figureDirectory = Join-Path $workspacePath 'figures'
$paperDirectory = Join-Path $workspacePath 'paper'
$mdb = Join-Path $sourceDirectory '<SOURCE_FILE_REDACTED>'

if ($Bootstrap -lt 1) {
    throw 'Bootstrap replicate count must be positive.'
}

python $phaseCheck --workspace $workspacePath
if ($LASTEXITCODE -ne 0) {
    throw 'Phase-lock check failed.'
}

[System.IO.Directory]::CreateDirectory($sourceDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($stagingDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($resultDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($figureDirectory) | Out-Null

# Exact generated files are removed before computation so a failed step cannot
# leave a stale manifest or verification report looking current.
foreach ($relative in @(
    'results\checksums.sha256',
    'results\manifest-verification.json',
    'results\paper-consistency.json',
    'results\consecutive-rerun.json'
)) {
    [System.IO.File]::Delete((Join-Path $workspacePath $relative))
}

function Invoke-ModelAndPaper {
    tar -xf $archive -C $sourceDirectory
    if ($LASTEXITCODE -ne 0) {
        throw 'Archive extraction failed.'
    }

    & (Join-Path $PSScriptRoot 'extract_mdb.ps1') `
        -InputMdb $mdb `
        -OutputDirectory $stagingDirectory
    if ($LASTEXITCODE -ne 0) {
        throw 'MDB extraction failed.'
    }

    python (Join-Path $PSScriptRoot 'solve.py') `
        --data-dir $stagingDirectory `
        --output-dir $resultDirectory `
        --figure-dir $figureDirectory `
        --bootstrap $Bootstrap `
        --seed $Seed
    if ($LASTEXITCODE -ne 0) {
        throw 'Model computation failed.'
    }

    python (Join-Path $PSScriptRoot 'independent_verify.py') --workspace $workspacePath
    if ($LASTEXITCODE -ne 0) {
        throw 'Independent verification failed.'
    }

    # Fix the build epoch for same-environment byte reproducibility.  No
    # cross-platform PDF identity is claimed.
    $env:SOURCE_DATE_EPOCH = '1096588800'
    $env:FORCE_SOURCE_DATE = '1'
    $env:TZ = 'UTC'
    Push-Location $paperDirectory
    try {
        xelatex -interaction=nonstopmode -halt-on-error -file-line-error main.tex
        if ($LASTEXITCODE -ne 0) {
            throw 'First XeLaTeX pass failed.'
        }
        xelatex -interaction=nonstopmode -halt-on-error -file-line-error main.tex
        if ($LASTEXITCODE -ne 0) {
            throw 'Second XeLaTeX pass failed.'
        }
    }
    finally {
        Pop-Location
    }
}

$rerunFiles = @(
    'work\staging\<SOURCE_FILE_REDACTED>',
    'work\staging\<SOURCE_FILE_REDACTED>',
    'work\staging\<SOURCE_FILE_REDACTED>',
    'work\staging\extraction-manifest.json',
    'results\data-audit.json',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\<SOURCE_FILE_REDACTED>',
    'results\validation.json',
    'results\summary.json',
    'results\environment.json',
    'results\independent-verification.json',
    'results\tables\survey-key.tex',
    'results\tables\key-values.tex',
    'results\tables\flow-allocation.tex',
    'results\tables\flow-allocation.md',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'figures\<SOURCE_FILE_REDACTED>',
    'paper\<SOURCE_FILE_REDACTED>'
)

Invoke-ModelAndPaper
$firstHashes = [ordered]@{}
foreach ($relative in $rerunFiles) {
    $target = Join-Path $workspacePath $relative
    if (-not [System.IO.File]::Exists($target)) {
        throw "First run did not create required artifact: $relative"
    }
    $firstHashes[$relative.Replace('\', '/')] = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
}

Invoke-ModelAndPaper
$mismatches = @()
$secondHashes = [ordered]@{}
foreach ($relative in $rerunFiles) {
    $key = $relative.Replace('\', '/')
    $target = Join-Path $workspacePath $relative
    if (-not [System.IO.File]::Exists($target)) {
        $mismatches += [ordered]@{ path = $key; reason = 'missing_second_run'; status = 'fail' }
        continue
    }
    $secondHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
    $secondHashes[$key] = $secondHash
    if ($firstHashes[$key] -ne $secondHash) {
        $mismatches += [ordered]@{
            path = $key
            first_sha256 = $firstHashes[$key]
            second_sha256 = $secondHash
            status = 'fail'
        }
    }
}
$rerunStatus = if ($mismatches.Count -eq 0) { 'pass' } else { 'fail' }
$pdfKey = 'paper/<SOURCE_FILE_REDACTED>'
$pdfStatus = if ($firstHashes[$pdfKey] -eq $secondHashes[$pdfKey]) { 'pass' } else { 'fail' }
$rerunReport = [ordered]@{
    phase = 'blind-revision'
    status = $rerunStatus
    seed = $Seed
    bootstrap_replicates = $Bootstrap
    compared_files = $rerunFiles.Count
    same_environment_pdf_byte_identity_status = $pdfStatus
    cross_platform_bitwise_identity_status = 'needs_review'
    mismatches = $mismatches
    first_run_sha256 = $firstHashes
    second_run_sha256 = $secondHashes
}
$rerunJson = $rerunReport | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText(
    (Join-Path $resultDirectory 'consecutive-rerun.json'),
    $rerunJson + [Environment]::NewLine,
    (New-Object System.Text.UTF8Encoding($false))
)
if ($rerunStatus -ne 'pass') {
    throw 'Consecutive rerun comparison failed; see results/consecutive-rerun.json.'
}

python (Join-Path $PSScriptRoot 'check_consistency.py') --workspace $workspacePath
if ($LASTEXITCODE -ne 0) {
    throw 'Paper-result consistency check failed.'
}

python (Join-Path $PSScriptRoot 'artifact_manifest.py') --workspace $workspacePath --write
if ($LASTEXITCODE -ne 0) {
    throw 'Artifact manifest creation failed.'
}
python (Join-Path $PSScriptRoot 'artifact_manifest.py') `
    --workspace $workspacePath `
    --verify `
    --report 'results\manifest-verification.json'
if ($LASTEXITCODE -ne 0) {
    throw 'Artifact manifest verification failed.'
}

Write-Output '[pass] Full blind-revision pipeline completed; output remains unfrozen.'
