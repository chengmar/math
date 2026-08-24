param(
    [string]$Workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [int]$Bootstrap = 500,
    [int]$Seed = 2004
)

$ErrorActionPreference = 'Stop'
$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$archive = Join-Path $workspacePath 'input\attachments\<SOURCE_FILE_REDACTED>'
$sourceDirectory = Join-Path $workspacePath 'work\source'
$stagingDirectory = Join-Path $workspacePath 'work\staging'
$resultDirectory = Join-Path $workspacePath 'results'
$figureDirectory = Join-Path $workspacePath 'figures'
$mdb = Join-Path $sourceDirectory '<SOURCE_FILE_REDACTED>'

[System.IO.Directory]::CreateDirectory($sourceDirectory) | Out-Null
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

Write-Output '[pass] Full pipeline completed.'
