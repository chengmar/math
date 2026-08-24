---
name: cumcm-a-audit
description: 对已通过哈希校验的 blind-v1 冻结解答执行完全独立、不可改写的复现与审计。仅当用户显式输入 $cumcm-a-audit 且 phase-lock 为 audit 时使用；不得用于求解、盲修订、参考复盘或盲测，也不得调用其他阶段 Skill。
---

# CUMCM A 独立审计

先运行 `scripts/check_phase.py --workspace <当前工作区>`。读取题目、原始数据、冻结解答和复现说明；禁止读取 Vault、获奖论文、讲评或标准答案，禁止改写 `frozen-solution`。

## 工作流

1. 先验证冻结清单、逐文件 SHA-256 和 phase lock；不一致即停止。
2. 在干净环境复跑代码，记录依赖、命令、stdout、stderr、退出码和输出。
3. 审查小问覆盖、公式推导、单位量纲、约束、边界/初值、可辨识性、可行/有界性和收敛。
4. 检查随机稳定性、泄漏、验证集混用、因果误读、过拟合、异常敏感性及论文—代码—图表一致性。
5. 构造反例、极端输入或简化案例，并寻找更简单但同样有效的模型。
6. 只给证据化修订建议，不修改冻结解答。

## 读写边界与完成标准

只读 `input` 和 `frozen-solution`；只写 audit 工作区根目录。不得调用 `$cumcm-a-solve`、`$cumcm-a-reflect` 或 `$cumcm-a-evaluate`。生成 `audit-report.md`、`audit-findings.yaml`、`reproduction-report.json`、`counterexamples.md`、`revision-plan.md`。每条 finding 含 id、severity、category、evidence、affected_files、reason、recommended_fix、requires_human_review，严重度仅用 critical/major/minor/suggestion。
