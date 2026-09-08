# 阶段 1：Solve

单次正式调用，硬上限 21600 秒。完成全部小问的独立盲解并持续写入：

- `checkpoint/problem-analysis.md`
- `checkpoint/data-audit.md`
- `checkpoint/assumptions.yaml`
- `checkpoint/variables.yaml`
- `checkpoint/model-selection.md`
- `submission/blind-v1/code/`
- `submission/blind-v1/results/`
- `submission/blind-v1/figures/`
- `submission/blind-v1/paper/`
- `submission/blind-v1/reproducibility.yaml`
- `submission/blind-v1/solution-report.yaml`
- `submission/blind-v1/stage-status.json`

先审计数据、单位与缺失，再建立简单基线；比较不超过三个主要候选模型，选择最简充分方案。给出变量、假设、推导、固定随机种子实现、机器可读结果、独立验证、反例或边界检查、稳健性与误差分析，并完成论文及构建。每完成一类成果即落盘，不等待终答后再集中写入。

`stage-status.json` 必须列出小问覆盖、关键输出、复现命令、PDF 状态、已完成项和未完成项。只有可复现且合同完整的目录才可冻结为 `FROZEN_BLIND_V1`。

