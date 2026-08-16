param(
    [Parameter(Mandatory = $true)][string]$CaseId,
    [Parameter(Mandatory = $true)][ValidateSet('solve','audit','blind-revision','reflection','evaluation')][string]$Phase,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$trainerRoot = Split-Path -Parent $PSScriptRoot
$labRoot = Split-Path -Parent $trainerRoot
$venvPython = Join-Path $trainerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { $venvPython = (Get-Command python).Source }
$workflow = Join-Path $trainerRoot 'tools\workflow.py'
$workspace = (& $venvPython $workflow prepare --root $trainerRoot --case-id $CaseId --phase $Phase | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path -LiteralPath $workspace)) {
    Write-Error "阶段工作区未创建：$workspace"
    exit 1
}
$env:CODEX_HOME = Join-Path $labRoot 'codex-home'
$lock = Get-Content -LiteralPath (Join-Path $workspace 'phase-lock.json') -Raw | ConvertFrom-Json
Write-Host "阶段：$Phase"
Write-Host "唯一应调用的 Skill：$($lock.skill)"
Write-Host "CODEX_HOME=$env:CODEX_HOME"
Write-Host '允许资源：'
Get-Content -LiteralPath (Join-Path $workspace 'allowed-paths.json')
Write-Host '禁止资源：'
Get-Content -LiteralPath (Join-Path $workspace 'forbidden-paths.json')
if ($NoLaunch) {
    Write-Host '[PASS] 工作区已准备；按 -NoLaunch 未启动 Codex。'
    exit 0
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    Write-Error '未找到 codex 命令。'
    exit 1
}
Set-Location -LiteralPath $workspace
Write-Host '即将启动全新 Codex 会话；请复制 prompts 中对应提示词并显式调用上述 Skill。'
& codex
exit $LASTEXITCODE

