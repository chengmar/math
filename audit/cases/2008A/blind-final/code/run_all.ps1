param(
    [ValidateRange(2, 100000)]
    [int]$MonteCarlo = 100,
    [int]$Seed = 2008
)

$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$results = Join-Path $workspace 'results'
$stdoutLog = Join-Path $results 'run_all.stdout.log'
$stderrLog = Join-Path $results 'run_all.stderr.log'
$utf8 = New-Object System.Text.UTF8Encoding($false)
New-Item -ItemType Directory -Force -Path $results | Out-Null
[System.IO.File]::WriteAllText($stdoutLog, '', $utf8)
[System.IO.File]::WriteAllText($stderrLog, '', $utf8)

function Invoke-LoggedStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    [System.IO.File]::AppendAllText($stdoutLog, "===== $Name =====`r`n", $utf8)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Native tools such as MiKTeX may emit non-fatal diagnostics on stderr.
        # Preserve stderr in its log and decide success only from the exit code.
        $ErrorActionPreference = 'Continue'
        & $Action 1>> $stdoutLog 2>> $stderrLog
        $stepExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($null -eq $stepExitCode) { $stepExitCode = 0 }
    if ($stepExitCode -ne 0) {
        throw "$Name failed with exit code $stepExitCode; inspect results/run_all.stderr.log"
    }
}

Push-Location $workspace
try {
    Invoke-LoggedStep 'extract_problem' {
        & powershell -NoProfile -ExecutionPolicy Bypass -File 'code\extract_problem.ps1'
    }
    Invoke-LoggedStep 'solve' {
        & python -B 'code\solve.py' '--monte-carlo' $MonteCarlo '--seed' $Seed
    }
    Invoke-LoggedStep 'extreme_checks' {
        & python -B 'code\check_extremes.py'
    }
    Invoke-LoggedStep 'paper_result_consistency' {
        & python -B 'code\check_consistency.py'
    }
    Invoke-LoggedStep 'xelatex_pass_1' {
        Push-Location 'paper'
        try { & xelatex '-interaction=nonstopmode' '-halt-on-error' 'main.tex' }
        finally { Pop-Location }
    }
    Invoke-LoggedStep 'xelatex_pass_2' {
        Push-Location 'paper'
        try { & xelatex '-interaction=nonstopmode' '-halt-on-error' 'main.tex' }
        finally { Pop-Location }
    }

    & python -B 'code\build_run_manifest.py' '--monte-carlo' $MonteCarlo '--seed' $Seed
    if ($LASTEXITCODE -ne 0) { throw "Run manifest build failed with exit code $LASTEXITCODE" }
    & python -B 'code\verify_run_manifest.py'
    if ($LASTEXITCODE -ne 0) { throw "Run manifest verification failed with exit code $LASTEXITCODE" }
    Write-Output 'FULL_PIPELINE=pass'
}
finally {
    Pop-Location
}
