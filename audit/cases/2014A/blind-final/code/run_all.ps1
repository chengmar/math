param(
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot

if (-not $PythonExecutable) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        $PythonExecutable = $cmd.Source
    } else {
        $runtimePython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
        if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
            $PythonExecutable = $runtimePython
        } else {
            throw "Python executable not found; pass -PythonExecutable explicitly."
        }
    }
}

Push-Location $workspace
try {
    & $PythonExecutable -I -B "code\build.py"
    if ($LASTEXITCODE -ne 0) { throw "build.py failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
