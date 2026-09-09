---
name: cumcm-a-evaluate
description: 对开发集或最终考试案例执行严格盲测、记录运行元数据并冻结评测提交。仅当用户显式输入 $cumcm-a-evaluate 且 phase-lock 为 evaluation 时使用；不得读取答案、参考论文、candidate 或当前题复盘，不得修改规则、Skills、knowledge 或历史记录，也不得调用其他阶段 Skill。
---

# CUMCM A 盲测评估

先运行 `scripts/check_phase.py --workspace <当前工作区>`。只读题目、原始附件、verified 知识、模板和协议允许的公开资料。答案与评分依据在提交冻结前绝不进入工作区。

## 工作流

1. 记录开始/结束时间、Codex 版本、模型、推理设置、允许工具、时间预算和知识卡 ID。
2. 按盲解纪律完成可重跑代码、结果、验证、稳健性和论文，但不得修改系统本身。
3. 使用固定评分协议保存完整输出；结束后生成 `evaluation-submission.json` 并冻结提交。
4. 提交冻结后才允许在独立评分工作区导入答案；不得回写或补改原提交。
5. 输出客观检查结果和待人工评分项；评测经验不得直接进入 knowledge。

## 读写边界与完成标准

只读 evaluation 工作区的 `input`、`knowledge`、`paper-template` 和控制清单；只写当前工作区。不得调用其他三个阶段 Skill。只有运行元数据完整、关键结果可重跑、提交清单已生成且原提交停止改写时才完成。
