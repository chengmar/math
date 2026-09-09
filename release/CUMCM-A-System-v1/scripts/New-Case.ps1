[CmdletBinding()]
param([string]$CaseId,[string[]]$ProblemFiles,[string[]]$DataFiles=@())
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $CaseId) { $CaseId = Read-Host '请输入案例 ID' }
if (-not $ProblemFiles) { $ProblemFiles = @((Read-Host '请输入题面文件完整路径')) }
$args = @('new-case','--id',$CaseId)
foreach($file in $ProblemFiles){$args += @('--problem',$file)}
foreach($file in $DataFiles){$args += @('--data',$file)}
Invoke-SystemCli @args
