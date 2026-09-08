# 阶段 2：Audit

单次独立调用，硬上限 14400 秒。只读取当前臂官方输入和已冻结 `FROZEN_BLIND_V1`，只审计、不修改提交。

输出到 `audit/`：

- `findings.json`
- `findings.md`
- `counterexamples/`
- `revision-plan.md`
- `reproduction-report.json`
- `stage-status.json`

逐小问核查数学正确性、关键数值、单位、约束、边界、可辨识性、稳定性、反例、复现、复杂度收益和论文一致性。每条 finding 必须有唯一 ID、严重度 `critical/high/medium/low`、证据、影响文件/结果、可执行修订和验证方式。复现必须使用冻结 V1 的声明命令，禁止修复代码。

