param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$workspaceFull = [System.IO.Path]::GetFullPath($Workspace).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$workspacePrefix = $workspaceFull + [System.IO.Path]::DirectorySeparatorChar
foreach ($relativeTarget in @('results', 'figures', 'paper\generated')) {
    $target = [System.IO.Path]::GetFullPath((Join-Path $workspaceFull $relativeTarget))
    if (-not $target.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean target outside workspace: $target"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

& (Join-Path $PSScriptRoot 'extract_input.ps1') -Workspace $Workspace
python (Join-Path $PSScriptRoot 'solve.py')
if ($LASTEXITCODE -ne 0) {
    throw "solve.py failed with exit code $LASTEXITCODE"
}

Write-Output 'pass: extraction, analysis, validation and figure generation completed'
