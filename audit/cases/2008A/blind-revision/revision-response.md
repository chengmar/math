# 独立审计响应

- case_id: `2008A`
- phase: `blind-revision`
- overall_status: `pass`（仅指本阶段可完成的修订）
- external_freeze_status: `needs_review`
- independent_reaudit_status: `needs_review`

## 修订边界

本版只读取原题、原始附件、冻结 V1 与独立审计。本阶段没有读取参考论文、
赛题讲评、答案或 Vault，没有联网，也没有调用其他阶段 Skill。`blind-v1/`
保留不动；修订产物写在当前工作区。数学内容审计在上一轮因冻结前置门失败
而未执行，因此本版不声称已经获得独立数学审计结论。

交付前只读复核得到 `blind-v1/` 仍有 178 个普通文件、5,399,327 字节，
且其 `code/solve.py` 的 SHA-256 为 `f3fc1ad7...c3017`，与审计记录一致：
`pass`。这只是已有审计字段的可观察一致性检查；因 V1 原本没有整树清单，
逐文件冻结同一性仍不可反向伪造。

## Finding 处置

| Finding | 处置 | 证据 | 状态 |
|---|---|---|---|
| `AUD-PREFLIGHT-001`：没有整树冻结清单 | 不在 solve 阶段自行冻结。运行清单显式声明其范围与排除规则；完整规范化路径、字节数、SHA-256、冻结标识和时间由外部 `blind-final` 冻结脚本生成 | `results/run_manifest.json` 的 `manifest_scope`；`solution-report.yaml` 的 `delivery` | needs_review |
| `AUD-PREFLIGHT-002`：旧清单代码哈希与当前代码冲突 | 选择当前 V1 `code/solve.py` 为权威版本，不尝试伪造或恢复未知哈希；从原题重新提取并重建所有规范产物，清单重新计算权威代码与每个产物哈希，写后逐项复核 | `results/run_manifest.json`；`results/run_manifest_verification.json`；`results/logs/` | pass |
| `AUD-PREFLIGHT-003`：Monte Carlo 与 bootstrap 两套协议 | 退役旧 `--bootstrap 120` 和 `results/generated/`，修订版不复制其产物。唯一顶层入口连续两次运行 `python code/solve.py --monte-carlo 100`，固定种子 2008 | `code/run_all.ps1`；`code/run_all.py`；`results/repeatability.json` | pass |

## 交付给外部冻结流程的门禁

外部脚本生成 `blind-final` 前应把以下项目分别判定，不得合并：

1. 当前 `results/run_manifest_verification.json.status` 为 `pass`；
2. 当前 `results/repeatability.json.status` 为 `pass`；
3. 对最终完整载荷生成整树清单，采用规范化相对路径，记录字节数和 SHA-256，
   并明确清单自身排除规则；
4. 生成唯一冻结标识和冻结时间后，再把冻结状态判为 `pass`；
5. 把新冻结载荷复制到全新的最小 audit 工作区，重新执行独立审计。

第 3–5 项在本阶段均为 `needs_review`。
