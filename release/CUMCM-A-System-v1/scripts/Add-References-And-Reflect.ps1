[CmdletBinding()]
param([string]$CaseId,[string[]]$ReferenceFiles,[ValidateSet('codex','fake')][string]$Executor='codex')
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $CaseId) { $CaseId = Read-Host '请输入案例 ID' }
if (-not $ReferenceFiles) {
    $ReferenceFiles = @()
    do { $value = Read-Host '输入参考论文路径（完成请直接回车）'; if($value){$ReferenceFiles += $value} } while($value)
}
$args=@('add-references','--id',$CaseId,'--executor',$Executor)
foreach($file in $ReferenceFiles){$args += @('--reference',$file)}
Invoke-SystemCli @args
