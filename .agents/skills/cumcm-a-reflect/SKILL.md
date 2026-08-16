---
name: cumcm-a-reflect
description: 在 blind-final 已冻结且独立审计完成后，对受控导入的两至四篇优秀参考材料做批判性对照并提出 candidate 知识。仅当用户显式输入 $cumcm-a-reflect 且 phase-lock 为 reflection 时使用；不得用于盲解、审计、评测或直接升级 verified，也不得调用其他阶段 Skill。
---

# CUMCM A 参考复盘

先运行 `scripts/check_phase.py --workspace <当前工作区>`。只读 `blind-final` 与 `approved-references`；不得覆盖冻结历史、不得默认参考论文正确、不得复制其语言或结构，也不得为了对齐参考结果篡改盲解记录。

## 工作流

1. 验证最终冻结清单和审计前提；记录每份参考材料及其校验状态。
2. 尽量比较 2–4 篇，复跑其代码或重实现关键计算；没有证据时标记 needs_review。
3. 比较问题拆解、假设、数据、基线、主模型、参数、算法、验证、敏感性、误差、结果、复杂度、解释性、稳健性和论文表达。
4. 区分独立想到、审计后修正、看参考后才学到三类内容。
5. 提炼本题特有信息、candidate 方法卡、失败模式、验证模式、表达经验和不应推广经验。
6. 未经文献检索不得宣称世界首创；“新颖”只相对当前比较集或实用收益描述。

## 读写边界与完成标准

只写 reflection 工作区和其中 `lessons-proposed/`；不得写核心 knowledge 或冻结目录，不得调用其他三个阶段 Skill。生成 `comparison-matrix.md/.yaml`、`reference-validation.md`、`self-gap-analysis.md`、`innovation-analysis.md`、`lessons-proposed/`。所有单题经验状态必须为 `candidate` 或 `demo`，不能是 verified。

