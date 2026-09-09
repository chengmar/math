[CmdletBinding()]
param([string]$CaseId,[ValidateSet('codex','fake')][string]$Executor='codex')
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $CaseId) { $CaseId = Read-Host '请输入案例 ID' }
Invoke-SystemCli resume --id $CaseId --executor $Executor
