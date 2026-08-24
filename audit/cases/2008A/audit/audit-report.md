# 2008A 冻结解答独立审计报告

- case_id: `2008A`
- phase: `audit`
- audited_at: `2026-08-23T18:45:07.7595372+08:00`
- overall_status: `fail`
- stop_point: `freeze_integrity_preflight`

## 结论

冻结完整性前置门为 `fail`。允许范围内的 `frozen-solution` 共含 178 个普通文件、5,399,327 字节，但不存在覆盖整份冻结解答的 `FROZEN`/冻结清单；现有 `solution-report.yaml` 还明确记录 `delivery.frozen: false`。此外，局部运行清单 `results/generated/run_manifest.json` 声明的 `code_sha256` 与当前 `code/solve.py` 的实测 SHA-256 不一致。

依据 `$cumcm-a-audit` 的“冻结清单或逐文件哈希不一致即停止”规则，本次没有启动干净环境复现，也没有继续作数学正确性、量纲、约束、数值稳定性、泄漏、过拟合、反例或论文—代码一致性审查。上述未执行项目均为 `needs_review`，不能据此声称数学正确或可复现。

## 前置检查

| 检查项 | 状态 | 证据 |
|---|---|---|
| audit 阶段锁 | `pass` | `check_phase.py` 退出码 0，stdout 为 `[PASS] audit 阶段锁有效` |
| audit 的 allowed 清单哈希 | `pass` | 实测 `79da142ab0281b06e8a2ad0c346f05d1b9c9bf61ea5cf69ee6eb0a00df73a07b`，与 `phase-lock.json` 一致 |
| audit 的 forbidden 清单哈希 | `pass` | 实测 `f075bf741aa5e0dc4527893642bc07eb4bc70edc84733fa9b1801ed5ccaa0b47`，与 `phase-lock.json` 一致 |
| 外层原题文件哈希 | `pass` | 实测 `d28c0ef2aaee74e9618dbf77a022527ced5f8b723eee4cc27774f648d842e599` |
| 整树冻结清单存在性 | `fail` | 178 个文件中，名称搜索仅发现运行级 `results/generated/run_manifest.json`；未发现独立 `FROZEN`/freeze/checksum 清单 |
| 冻结声明 | `fail` | `solution-report.yaml` 的 `delivery.frozen` 为 `false` |
| 冻结逐文件 SHA-256 覆盖 | `fail` | 无法把 178 个文件逐项与冻结清单比对；两个现有清单都只覆盖局部输入/产物 |
| `reproducibility.yaml` 已声明哈希 | `pass` | 本地允许路径映射后 10/10 相符；这不是整树冻结证明 |
| `run_manifest.json` 已声明输出哈希 | `pass` | 17/17 相符；这不是整树冻结证明 |
| 当前代码与运行清单代码哈希 | `fail` | 期望 `a6f8bdf079646589bb812cba7d0503a2ba93db6442de65333fa41cab6fff40c6`；实测 `f3fc1ad7b06e1ac71bb5b5db3ea0bf19b6c5d71787959a2cef4b1bb6c41c3017` |

## 未执行范围

| 项目 | 状态 | 原因 |
|---|---|---|
| 干净环境依赖安装与复跑 | `needs_review` | 冻结完整性门失败，按阶段 Skill 停止 |
| stdout、stderr、退出码与新输出比对 | `needs_review` | 未运行冻结代码 |
| 随机种子与重复运行稳定性 | `needs_review` | 未运行冻结代码 |
| 数学推导、量纲、约束、边界与可辨识性 | `needs_review` | 未进入内容审查 |
| 泄漏、过拟合、异常敏感性与数值稳定性 | `needs_review` | 未进入内容审查 |
| 论文—代码—图表一致性 | `needs_review` | 未进入内容审查 |
| 数学反例和极端输入 | `needs_review` | 未进入内容审查 |

## 边界遵守

| 项目 | 状态 |
|---|---|
| 未联网搜索当前题目、题名、答案或现成解答 | `pass` |
| 未读取 reference-vault、exam-vault 或参考论文 | `pass` |
| 未调用其他阶段 Skill | `pass` |
| 未修改 `frozen-solution` | `pass` |

详细 finding 见 `audit-findings.yaml`；机器可读复现记录见 `reproduction-report.json`。
