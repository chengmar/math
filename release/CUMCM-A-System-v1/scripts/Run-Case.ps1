[CmdletBinding()]
param([string]$CaseId,[ValidateSet('codex','fake')][string]$Executor='codex',[int]$MaxStages=0)
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $CaseId) { $CaseId = Read-Host '请输入案例 ID' }
$args=@('run','--id',$CaseId,'--executor',$Executor)
if($MaxStages -gt 0){$args += @('--max-stages',[string]$MaxStages)}
Invoke-SystemCli @args
