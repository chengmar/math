# 快速开始

```powershell
.\scripts\Verify-System.ps1
.\scripts\New-Case.ps1 -CaseId my-a-case -ProblemFiles .\my-problem.pdf -DataFiles .\my-data.xlsx
.\scripts\Run-Case.ps1 -CaseId my-a-case
.\scripts\Case-Status.ps1 -CaseId my-a-case
```

需要暂停时运行 `Pause-Case.ps1`；它会在当前模型阶段结束后的安全边界生效。之后运行 `Resume-Case.ps1`。Blind Final 验证通过后，使用 `Add-References-And-Reflect.ps1` 明确提供 2–4 篇参考材料，最后用 `Export-Submission.ps1` 导出。
