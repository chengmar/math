param([switch]$SkipInstall)

$ErrorActionPreference = 'Stop'
$trainerRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $trainerRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'

Set-Location -LiteralPath $trainerRoot
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Error '未找到 Python。'
        exit 1
    }
    python -m venv $venvRoot
}
if (-not $SkipInstall) {
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $trainerRoot 'requirements-core.txt')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $venvPython (Join-Path $trainerRoot 'tools\inventory_skills.py') --root $trainerRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[PASS] 核心环境就绪：$venvPython"
Write-Host '未安装 requirements-modeling.txt；首次真实题需要时再安装。'
