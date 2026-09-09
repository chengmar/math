[CmdletBinding()]
param([string]$SystemRoot = (Join-Path $PSScriptRoot 'release\CUMCM-A-System-v1'))

$ErrorActionPreference='Stop'
$scripts=Join-Path $SystemRoot 'scripts'
if(-not (Test-Path -LiteralPath $scripts)){
    Write-Host "未找到产品包：$SystemRoot" -ForegroundColor Red
    Write-Host '恢复建议：把启动器放在 release 文件夹上一级，或用 -SystemRoot 指定解压后的 CUMCM-A-System-v1。'
    Read-Host '按回车退出' | Out-Null
    return
}
do {
    Clear-Host
    Write-Host 'CUMCM-A-System v1'
    Write-Host '1. 验证系统安装'
    Write-Host '2. 创建新A题案例'
    Write-Host '3. 开始或继续盲解'
    Write-Host '4. 查看案例状态'
    Write-Host '5. 安全暂停'
    Write-Host '6. 从断点恢复'
    Write-Host '7. 验证Blind Final'
    Write-Host '8. Blind Final后导入参考论文并复盘'
    Write-Host '9. 导出论文和支撑材料'
    Write-Host '10. 查看系统效果报告'
    Write-Host '11. 切换workflow-only/full-trained模式'
    Write-Host '0. 退出'
    $choice=Read-Host '请选择'
    try {
        switch($choice){
            '1' { & (Join-Path $scripts 'Verify-System.ps1') }
            '2' { & (Join-Path $scripts 'New-Case.ps1') }
            '3' { & (Join-Path $scripts 'Run-Case.ps1') }
            '4' { & (Join-Path $scripts 'Case-Status.ps1') }
            '5' { & (Join-Path $scripts 'Pause-Case.ps1') }
            '6' { & (Join-Path $scripts 'Resume-Case.ps1') }
            '7' { $id=Read-Host '案例 ID'; . (Join-Path $scripts '_Common.ps1'); Invoke-SystemCli verify-final --id $id }
            '8' { & (Join-Path $scripts 'Add-References-And-Reflect.ps1') }
            '9' { & (Join-Path $scripts 'Export-Submission.ps1') }
            '10' { Start-Process -FilePath (Join-Path $SystemRoot 'EVALUATION-SUMMARY.md') }
            '11' { & (Join-Path $scripts 'Set-Mode.ps1') }
            '0' { break }
            default { Write-Warning '无效选择；请输入 0—11。' }
        }
    } catch {
        Write-Host "操作失败：$($_.Exception.Message)" -ForegroundColor Red
        Write-Host '恢复建议：先运行菜单 1；再用菜单 4 查看案例状态。不要删除活动 lock 或冻结目录。' -ForegroundColor Yellow
    }
    if($choice -ne '0'){Read-Host '按回车继续' | Out-Null}
} while($choice -ne '0')
