param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipPdf
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'PowerShell 7 or newer is required. Run with: pwsh -NoProfile -File code/run_all.ps1'
}

$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$separator = [System.IO.Path]::DirectorySeparatorChar
if ($workspacePath -eq [System.IO.Path]::GetPathRoot($workspacePath)) {
    throw "Refusing to use a filesystem root as the workspace: $workspacePath"
}

function Assert-WorkspaceChild {
    param([string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($workspacePath + $separator)) {
        throw "Path is outside the intended workspace: $resolved"
    }
    return $resolved
}

function Reset-OutputDirectory {
    param([string]$RelativePath)
    $target = Assert-WorkspaceChild (Join-Path $workspacePath $RelativePath)
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    [void](New-Item -ItemType Directory -Path $target)
}

Reset-OutputDirectory 'results'
Reset-OutputDirectory 'figures'

$paperPath = Assert-WorkspaceChild (Join-Path $workspacePath 'paper')
$paperTransient = @(
    'main.aux', 'main.log', 'main.out', 'main.toc', 'main.xdv', '<SOURCE_FILE_REDACTED>'
)
foreach ($name in $paperTransient) {
    $path = Assert-WorkspaceChild (Join-Path $paperPath $name)
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}

$mplConfig = Assert-WorkspaceChild (Join-Path $workspacePath 'results\_mplconfig')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:MPLCONFIGDIR = $mplConfig
$env:SOURCE_DATE_EPOCH = '1787529600'
$env:TZ = 'UTC'

Push-Location $workspacePath
try {
    & (Join-Path $PSScriptRoot 'extract_inputs.ps1') -Workspace $workspacePath

    python (Join-Path $PSScriptRoot 'solve.py')
    if ($LASTEXITCODE -ne 0) { throw 'Numerical solution failed' }

    python (Join-Path $PSScriptRoot 'render_paper.py')
    if ($LASTEXITCODE -ne 0) { throw 'Paper rendering failed' }

    if (Test-Path -LiteralPath $mplConfig) {
        Remove-Item -LiteralPath $mplConfig -Recurse -Force
    }

    if (-not $SkipPdf) {
        Push-Location $paperPath
        try {
            xelatex --disable-installer -interaction=nonstopmode -halt-on-error main.tex
            if ($LASTEXITCODE -ne 0) { throw 'First XeLaTeX pass failed' }
            xelatex --disable-installer -interaction=nonstopmode -halt-on-error main.tex
            if ($LASTEXITCODE -ne 0) { throw 'Second XeLaTeX pass failed' }
        }
        finally {
            Pop-Location
        }
        foreach ($name in @('main.aux', 'main.log', 'main.out', 'main.toc', 'main.xdv')) {
            $path = Assert-WorkspaceChild (Join-Path $paperPath $name)
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force
            }
        }
    }

    python (Join-Path $PSScriptRoot 'verify_independent.py')
    if ($LASTEXITCODE -ne 0) { throw 'Independent numerical verification failed' }

    python (Join-Path $PSScriptRoot 'verify_outputs.py')
    if ($LASTEXITCODE -ne 0) { throw 'Structural output verification failed' }

    python (Join-Path $PSScriptRoot 'build_manifest.py')
    if ($LASTEXITCODE -ne 0) { throw 'Manifest generation failed' }

    python (Join-Path $PSScriptRoot 'build_manifest.py') --verify
    if ($LASTEXITCODE -ne 0) { throw 'Manifest verification failed' }

    Write-Output '[PASS] complete blind-revision pipeline'
}
finally {
    if (Test-Path -LiteralPath $mplConfig) {
        Remove-Item -LiteralPath $mplConfig -Recurse -Force
    }
    Pop-Location
}
