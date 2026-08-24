# 2006A 冻结解答独立审计报告

- 审计阶段：`audit`
- 使用 Skill：`$cumcm-a-audit`
- 审计时间：2026-08-22T21:59:52+08:00
- 总体状态：`fail`
- 冻结解答是否被本审计修改：否
- 是否进入干净环境复现：否

## 结论

审计在强制预检处停止。当前工作区通过了 audit 阶段锁检查，阶段级 `allowed-paths.json` 与 `forbidden-paths.json` 的 SHA-256 也与 `phase-lock.json` 一致。冻结解答内现有的 `results/input_hashes.json` 和 `results/output_hashes.json` 共列出 35 个文件；这 35 个文件的字节数和 SHA-256 均与清单一致。

但是，`frozen-solution` 共含 144 个文件，没有发现全量、权威的 `FROZEN` 清单或 `frozen-manifest`。现有两份哈希清单只覆盖 35 个文件，另有 109 个文件未覆盖，其中包括 `code/solve.py`、验证脚本、`paper/main.tex`、`paper/<SOURCE_FILE_REDACTED>`、`paper/paper.md`、`solution-report.yaml`、`reproducibility.yaml` 以及 `results/output_hashes.json` 自身。因而不能证明待复现的代码、论文和结果属于同一次已冻结提交。

依据 `$cumcm-a-audit` 的“先验证冻结清单、逐文件 SHA-256；不一致即停止”规则，本次不能进入复现、数学正确性审查或模型反例运行。此报告不据此断言解答在数学上正确或错误；这些项目均为 `needs_review`。

## 预检结果

| 检查 | 状态 | 证据 |
|---|---|---|
| audit 阶段锁 | `pass` | `check_phase.py` 输出 `[PASS] audit 阶段锁有效`，退出码 0 |
| audit 路径清单哈希 | `pass` | allowed：`c72d2517...f77999`；forbidden：`f075bf74...aa0b47`，均与阶段锁一致 |
| 冻结解答内 solve 阶段路径清单哈希 | `pass` | allowed 与 forbidden 两项均和内嵌 `phase-lock.json` 一致 |
| 外层输入与冻结副本 | `pass` | 题目 `.doc` 与附件 `.rar` 的 SHA-256 分别一致 |
| 现有输入/输出条目内部一致性 | `pass` | 35/35 个条目的字节数和 SHA-256 一致 |
| 权威全量 FROZEN 清单 | `fail` | 递归检查 144 个文件，未发现 `FROZEN*` 或 `frozen-manifest*`；现有清单仅覆盖 35 个 |
| 代码、论文及哈希清单的冻结身份 | `fail` | 109 个文件无权威基线，关键未覆盖文件见下文 |
| 干净环境复现 | `fail` | 因前置冻结验证失败而未执行；不得将“文件存在”当作复现成功 |

## 未受现有清单保护的关键文件

- `frozen-solution/code/solve.py`
- `frozen-solution/code/run_all.ps1`
- `frozen-solution/code/verify.py`
- `frozen-solution/code/verify_outputs.py`
- `frozen-solution/paper/main.tex`
- `frozen-solution/paper/<SOURCE_FILE_REDACTED>`
- `frozen-solution/paper/paper.md`
- `frozen-solution/solution-report.yaml`
- `frozen-solution/reproducibility.yaml`
- `frozen-solution/phase-lock.json`
- `frozen-solution/results/output_hashes.json`

当前观测哈希仅用于定位文件，不能代替缺失的权威冻结基线：

- `results/output_hashes.json`：`6939414dee901e4ac350e563fef77945677a032e6d0b5ec4ca1ff96afcb9ef9d`
- `code/solve.py`：`2bd675ac4ec009d6e25ea71bd185d00a00befd332531a14c96ae7e833f9ac747`
- `paper/main.tex`：`aa72976d43d0c20ea455328926673b04306b4ccc307c57dee8703413a603a38b`
- `paper/<SOURCE_FILE_REDACTED>`：`3efe712fc4c963d52a9d65fc5d0f0634db611f714d5b67853b7aca52a6fa0fa1`

## 审计域状态

| 审计域 | 状态 | 说明 |
|---|---|---|
| 小问覆盖与论文—代码一致性 | `needs_review` | 未越过冻结验证前置条件 |
| 公式、量纲、约束、边界与初值 | `needs_review` | 未执行数学审查 |
| 可辨识性、可行性、有界性与收敛 | `needs_review` | 未执行数学审查 |
| 随机稳定性、泄漏、验证集混用与过拟合 | `needs_review` | 未运行代码或数据实验 |
| 异常敏感性与模型反例 | `needs_review` | 仅完成冻结完整性的逻辑反例，未运行模型反例 |

## 发现摘要

1. `AUD-FROZEN-001`（critical）：缺少权威、全量冻结清单，无法建立待审计对象的身份。
2. `AUD-FROZEN-002`（major）：现有哈希清单只覆盖 35/144 个文件，且顶层输出哈希清单自身没有外部可信锚点。

完整字段见 `audit-findings.yaml`；复现命令与未执行原因见 `reproduction-report.json`；完整性反例见 `counterexamples.md`；修订建议见 `revision-plan.md`。
