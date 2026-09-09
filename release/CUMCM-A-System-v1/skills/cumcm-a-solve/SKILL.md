---
name: cumcm-a-solve
description: 执行 CUMCM A 题案例的完全盲解或仅依据独立审计报告的盲修订。仅当用户显式输入 $cumcm-a-solve 且当前 phase-lock 为 solve 或 blind-revision 时使用；不得用于独立审计、参考论文复盘、开发集/考试评测，也不得调用其他阶段 Skill。
---

# CUMCM A 盲解

先运行 `scripts/check_phase.py --workspace <当前工作区>`，检查失败立即停止。只读取 `allowed-paths.json` 列出的题目、原始附件、verified 知识、通用模板；盲修订时另可读取冻结 V1 和独立审计。禁止读取 Vault、参考论文、讲评、答案、reflection、candidate 或现成解答。

## 工作流

1. 解析每个小问，建立依赖图与“题目要求 → 变量 → 结果 → 论文位置”追踪表。
2. 审计格式、缺失、异常、单位和潜在泄漏；区分事实、假设、推断和工程判断。
3. 建立简单可解释基线；提出不超过三个主要候选，用适用条件、假设、数据、复杂度、解释性、稳健性和匹配度选型。
4. 固定随机种子，实现可重跑代码，记录依赖与命令并生成全部关键数字和图表。
5. 完成独立验证、边界/极端检查，以及敏感性、稳健性或误差分析；写明失效条件。
6. 生成论文源文档和结构化报告；逐项核对论文数字与代码输出。

## 读写边界

- 输入：工作区 `input`、`knowledge`、`paper-template`；盲修订另含 `blind-v1` 和 `audit`。
- 只写当前 solve 或 blind-revision 工作区；不得写 Vault、其他阶段、冻结目录或核心 knowledge。
- 不得调用 `$cumcm-a-audit`、`$cumcm-a-reflect` 或 `$cumcm-a-evaluate`。

## 必需输出与完成标准

生成 `problem-analysis.md`、`data-audit.md`、`assumptions.yaml`、`variables.yaml`、`model-selection.md`、`solution-report.yaml`、`reproducibility.yaml`、`code/`、`results/`、`figures/`、`paper/main.tex` 和 `paper/paper.md`。只有小问覆盖、代码重跑、验证/稳健性、局限性、论文—结果一致性均完成后才能交付；不要自行冻结或进入下一阶段。
