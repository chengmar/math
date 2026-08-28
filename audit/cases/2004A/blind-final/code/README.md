# 可重跑入口

在当前 `blind-revision` 工作区的 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\code\run_all.ps1 `
  -Workspace <RUNTIME_ROOT>\2004A\workspaces\blind-revision `
  -Bootstrap 500 -Seed 2004
```

流水线按固定顺序执行：阶段锁检查 → RAR/Access 只读提取 → 固定种子统计、最短路和整数配置 → 独立 Dijkstra 重算及极端输入检查 → XeLaTeX 两遍编译 → 同环境连续第二次完整重跑并逐文件比较 → 论文—结果一致性检查 → 最后生成 SHA-256 清单并逐项复核。

把 PDF 编译放在清单生成之前，是本次针对 `AUD-001` 的关键修订；清单验证报告为 `results/manifest-verification.json`。`solution-report.yaml` 在本阶段如实保留 `frozen: false`，外部冻结脚本建立 `blind-final` 时必须重新签发冻结元数据与清单。

环境要求：Python 3.11、`requirements.txt` 中的包、Windows PowerShell、`tar`、兼容的 Microsoft ACE/Jet OLE DB provider 和 XeLaTeX。脚本不会调用 audit、reflect 或 evaluate 阶段，也不会自行冻结。
