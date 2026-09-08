# 阶段 3：Revision-Correctness

单次独立调用，硬上限 14400 秒。读取当前臂冻结 V1 与本臂 Audit，只修复 critical/high 数学和实现问题：公式、代码、单位、约束、边界、稳定性、关键数值、反例与结果文件。不得全面润色论文或重新设计主模型，除非 Audit 的 critical finding 明确证明原模型不可用。

从冻结 V1 复制到 `submission/revision-work/` 后工作，禁止改动 `submission/blind-v1/`。每完成一个 finding，立即原子更新 `submission/revision-work/revision-progress.json`，字段包括 finding ID、状态、修改文件、重跑命令、验证输出和剩余问题；同时同步代码和机器可读结果。

输出 `correctness-stage-status.json`。未解决的 critical/high 必须明确保留，不得降级或隐藏。

