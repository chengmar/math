# 阶段 4：Revision-Paper-And-Verification

单次独立调用，硬上限 14400 秒。仅把阶段 3 已完成的修订写入论文，统一图表、摘要、符号、单位、结果引用、复现说明和 finding 映射，并完成最终交付检查。不得重新设计主模型；只有阶段 3 明确遗留 critical 时，才可做最小必要修复并显式记录。

在 `submission/revision-work/` 完成后生成：

- `submission/blind-final/`（代码、结果、图、论文源文件、PDF 或失败日志）
- `submission/blind-final/reproducibility.yaml`
- `submission/blind-final/solution-report.yaml`
- `submission/blind-final/revision-response.md`
- `submission/blind-final/finding-map.json`
- `submission/blind-final/stage-status.json`

逐条对应 Audit finding，检查论文中的所有关键数字均来自机器可读结果。终答前运行声明的复现、lint 与 XeLaTeX 构建，并把真实状态落盘。确定性 Revision Verification 由外部运行器执行，不得请求额外模型调用。

