# 可重跑入口

在当前 solve 工作区的 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File code\run_all.ps1
```

流程依次为：阶段锁检查 → RAR/Access 机械导出 → 固定种子统计与最短路/整数配置 → 独立路线台账重聚合 → 论文数字一致性 → XeLaTeX 两遍编译。

环境要求：Python 3.11、`requirements.txt` 中的包、PowerShell、`tar`、Microsoft ACE OLEDB 16.0 和 XeLaTeX。若只重跑数据与结果而跳过 PDF，可加 `-SkipPaperBuild`；此时论文编译状态只能记为 `needs_review`。

脚本不会冻结结果，也不会调用 audit、reflect 或 evaluate 阶段。
